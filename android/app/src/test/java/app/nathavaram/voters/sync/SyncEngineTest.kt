package app.nathavaram.voters.sync

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import app.nathavaram.voters.Fixtures
import app.nathavaram.voters.Fixtures.ID_1
import app.nathavaram.voters.Fixtures.ID_2
import app.nathavaram.voters.Fixtures.ID_3
import app.nathavaram.voters.Fixtures.T1
import app.nathavaram.voters.Fixtures.T2
import app.nathavaram.voters.Fixtures.T3
import app.nathavaram.voters.data.AppDatabase
import app.nathavaram.voters.data.ConflictEntity
import app.nathavaram.voters.data.SyncStateEntity
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.Instant

private class FakeSyncApi : SyncApi {
    val pushRequests = mutableListOf<PushRequest>()
    val pullSince = mutableListOf<Long>()
    val pages = mutableListOf<PullResponse>()
    var onPush: (PushRequest) -> PushResponse = { PushResponse(SERVER_TIME, 1, emptyList()) }

    override suspend fun push(request: PushRequest): PushResponse {
        pushRequests += request
        return onPush(request)
    }

    override suspend fun pull(since: Long, limit: Int): PullResponse {
        pullSince += since
        return if (pages.isEmpty()) PullResponse(SERVER_TIME, since, since, false, emptyList()) else pages.removeAt(0)
    }

    companion object {
        const val SERVER_TIME = "2026-11-11T12:00:00.000000Z"
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SyncEngineTest {
    private lateinit var db: AppDatabase
    private lateinit var api: FakeSyncApi
    private var deviceNow: Instant = Instant.parse(FakeSyncApi.SERVER_TIME)

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        api = FakeSyncApi()
    }

    @After
    fun tearDown() {
        db.close()
    }

    private fun engine() = SyncEngine(db, api, "android-test", now = { T3 }, nowInstant = { deviceNow })

    private fun page(vararg changes: VoterDto, hasMore: Boolean, serverRevision: Long = 99) =
        PullResponse(FakeSyncApi.SERVER_TIME, serverRevision, 0, hasMore, changes.toList())

    @Test
    fun `one conflict does not drop the rest of the batch`() = runBlocking {
        val dao = db.voterDao()
        dao.upsert(Fixtures.entity(ID_1, "ZZZ0000001", "Alpha", dirty = true, updatedAt = T1))
        dao.upsert(Fixtures.entity(ID_2, "ZZZ0000002", "Bravo", dirty = true, updatedAt = T2, revision = 2))
        dao.upsert(Fixtures.entity(ID_3, "ZZZ0000003", "Charlie", dirty = true, updatedAt = T3))
        api.onPush = {
            PushResponse(
                FakeSyncApi.SERVER_TIME, 12,
                listOf(
                    PushResult(ID_1, "applied", serverRevision = 10, serverRecord = Fixtures.dto(ID_1, "ZZZ0000001", "Alpha", updatedAt = T1, revision = 10)),
                    PushResult(ID_2, "conflict", message = "changed", serverRecord = Fixtures.dto(ID_2, "ZZZ0000002", "ServerBravo", updatedAt = T3, revision = 11)),
                    PushResult(ID_3, "validation_error", message = "rejected"),
                ),
            )
        }

        val summary = engine().run()

        assertEquals(1, summary.applied)
        assertEquals(1, summary.conflicts)
        assertEquals(1, summary.validationErrors)
        assertTrue(summary.needsAttention)
        val first = dao.get(ID_1)!!
        assertFalse(first.dirty)
        assertEquals(10L, first.revision)
        val second = dao.get(ID_2)!!
        assertEquals("ServerBravo", second.firstName)
        assertFalse(second.dirty)
        assertTrue(dao.get(ID_3)!!.dirty)
        assertEquals(setOf(ID_2, ID_3), db.conflictDao().all().first().map { it.voterUuid }.toSet())
    }

    @Test
    fun `push sends full snapshots with base_revision and the device id`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(ID_1, dirty = true, revision = 4))
        db.voterDao().upsert(Fixtures.entity(ID_2, "ZZZ0000002", dirty = true, revision = 0))

        engine().run()

        val request = api.pushRequests.single()
        assertEquals("android-test", request.deviceId)
        val byId = request.changes.associateBy { it.id }
        assertEquals(4L, byId.getValue(ID_1).baseRevision)
        assertNull(byId.getValue(ID_2).baseRevision)
        assertEquals("Male", byId.getValue(ID_1).gender)
    }

    @Test
    fun `nothing is pushed when no row is dirty`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(dirty = false))

        engine().run()

        assertTrue(api.pushRequests.isEmpty())
    }

    @Test
    fun `an applied push clears an earlier conflict note`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(dirty = true, updatedAt = T2))
        db.conflictDao().upsert(ConflictEntity(ID_1, "validation_error", "old", T1, null, null))
        api.onPush = {
            PushResponse(
                FakeSyncApi.SERVER_TIME, 3,
                listOf(PushResult(ID_1, "applied", serverRecord = Fixtures.dto(ID_1, updatedAt = T2, revision = 3))),
            )
        }

        engine().run()

        assertNull(db.conflictDao().get(ID_1))
    }

    @Test
    fun `pull continues until has_more is false and stores the cursor after each page`() = runBlocking {
        api.pages += page(
            Fixtures.dto(ID_1, "ZZZ0000001", revision = 1), Fixtures.dto(ID_2, "ZZZ0000002", revision = 2),
            hasMore = true,
        )
        api.pages += page(Fixtures.dto(ID_3, "ZZZ0000003", revision = 3), hasMore = false)

        val summary = engine().run()

        assertEquals(listOf(0L, 2L), api.pullSince)
        assertEquals(3, summary.pulled)
        assertEquals(3L, db.voterDao().cursor())
        assertNotNull(db.voterDao().get(ID_3))
    }

    @Test
    fun `an empty pull moves the cursor to the server revision`() = runBlocking {
        db.voterDao().putState(SyncStateEntity(serverRevision = 5))
        api.pages += PullResponse(FakeSyncApi.SERVER_TIME, 8, 5, false, emptyList())

        engine().run()

        assertEquals(listOf(5L), api.pullSince)
        assertEquals(8L, db.voterDao().cursor())
    }

    @Test
    fun `pull does not overwrite a dirty local row that is newer`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(dirty = true, updatedAt = T3, firstName = "LocalNewer", revision = 1))
        api.onPush = { PushResponse(FakeSyncApi.SERVER_TIME, 1, emptyList()) }
        api.pages += page(Fixtures.dto(ID_1, firstName = "ServerOlder", updatedAt = T2, revision = 7), hasMore = false)

        engine().run()

        val local = db.voterDao().get(ID_1)!!
        assertEquals("LocalNewer", local.firstName)
        assertTrue(local.dirty)
        assertEquals(7L, local.revision)
    }

    @Test
    fun `a pulled tombstone removes the voter from search without resurrecting it`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(dirty = false))
        api.pages += page(Fixtures.tombstoneDto(ID_1, revision = 9), hasMore = false)

        engine().run()

        val stored = db.voterDao().get(ID_1)!!
        assertNotNull(stored.deletedAt)
        assertEquals("", stored.firstName)
        assertTrue(db.voterDao().search("%", 10).first().isEmpty())
    }

    @Test
    fun `voter_id_conflict drops the unsynced local row and keeps the existing voter`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(ID_1, "ZZZ0000001", "Newcomer", dirty = true, revision = 0))
        api.onPush = {
            PushResponse(
                FakeSyncApi.SERVER_TIME, 5,
                listOf(PushResult(ID_1, "voter_id_conflict", message = "in use",
                    serverRecord = Fixtures.dto(ID_2, "ZZZ0000001", "Existing", revision = 4))),
            )
        }

        val summary = engine().run()

        assertEquals(1, summary.voterIdConflicts)
        assertNull(db.voterDao().get(ID_1))
        assertEquals("Existing", db.voterDao().get(ID_2)?.firstName)
        assertEquals("voter_id_conflict", db.conflictDao().get(ID_1)?.kind)
    }

    @Test
    fun `a phone clock far from server_time is flagged`() = runBlocking {
        deviceNow = Instant.parse("2026-11-11T13:00:00Z")

        val summary = engine().run()

        assertTrue(summary.clockSkew)
    }

    @Test
    fun `a phone clock close to server_time is not flagged`() = runBlocking {
        deviceNow = Instant.parse("2026-11-11T12:02:00Z")

        assertFalse(engine().run().clockSkew)
    }
}

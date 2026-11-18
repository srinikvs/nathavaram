package app.nathavaram.voters.sync

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import app.nathavaram.voters.Fixtures
import app.nathavaram.voters.Fixtures.VOTER_1
import app.nathavaram.voters.Fixtures.VOTER_2
import app.nathavaram.voters.Fixtures.VOTER_3
import app.nathavaram.voters.Fixtures.T1
import app.nathavaram.voters.Fixtures.T2
import app.nathavaram.voters.Fixtures.T3
import app.nathavaram.voters.store.AppDatabase
import app.nathavaram.voters.store.ConflictEntity
import app.nathavaram.voters.store.SyncStateEntity
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
@Config(sdk = [34], application = android.app.Application::class)
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
        dao.upsert(Fixtures.entity(VOTER_1, "Alpha", dirty = true, updatedAt = T1))
        dao.upsert(Fixtures.entity(VOTER_2, "Bravo", dirty = true, updatedAt = T2, revision = 2))
        dao.upsert(Fixtures.entity(VOTER_3, "Charlie", dirty = true, updatedAt = T3))
        api.onPush = {
            PushResponse(
                FakeSyncApi.SERVER_TIME, 12,
                listOf(
                    PushResult(VOTER_1, "applied", serverRevision = 10, serverRecord = Fixtures.dto(VOTER_1, "Alpha", updatedAt = T1, revision = 10)),
                    PushResult(VOTER_2, "conflict", message = "changed", serverRecord = Fixtures.dto(VOTER_2, "ServerBravo", updatedAt = T3, revision = 11)),
                    PushResult(VOTER_3, "validation_error", message = "rejected"),
                ),
            )
        }

        val summary = engine().run()

        assertEquals(1, summary.applied)
        assertEquals(1, summary.conflicts)
        assertEquals(1, summary.validationErrors)
        assertTrue(summary.needsAttention)
        val first = dao.get(VOTER_1)!!
        assertFalse(first.dirty)
        assertEquals(10L, first.revision)
        val second = dao.get(VOTER_2)!!
        assertEquals("ServerBravo", second.firstName)
        assertFalse(second.dirty)
        assertTrue(dao.get(VOTER_3)!!.dirty)
        assertEquals(setOf(VOTER_2, VOTER_3), db.conflictDao().all().first().map { it.voterId }.toSet())
    }

    @Test
    fun `push sends full snapshots with base_revision and the device id`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(VOTER_1, dirty = true, revision = 4))
        db.voterDao().upsert(Fixtures.entity(VOTER_2, dirty = true, revision = 0))

        engine().run()

        val request = api.pushRequests.single()
        assertEquals("android-test", request.deviceId)
        val byId = request.changes.associateBy { it.voterId }
        assertEquals(4L, byId.getValue(VOTER_1).baseRevision)
        assertNull(byId.getValue(VOTER_2).baseRevision)
        assertEquals("12-A", byId.getValue(VOTER_1).houseNo)
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
        db.conflictDao().upsert(ConflictEntity(VOTER_1, "validation_error", "old", T1, null, null))
        api.onPush = {
            PushResponse(
                FakeSyncApi.SERVER_TIME, 3,
                listOf(PushResult(VOTER_1, "applied", serverRecord = Fixtures.dto(VOTER_1, updatedAt = T2, revision = 3))),
            )
        }

        engine().run()

        assertNull(db.conflictDao().get(VOTER_1))
    }

    @Test
    fun `pull continues until has_more is false and stores the cursor after each page`() = runBlocking {
        api.pages += page(
            Fixtures.dto(VOTER_1, revision = 1), Fixtures.dto(VOTER_2, revision = 2),
            hasMore = true,
        )
        api.pages += page(Fixtures.dto(VOTER_3, revision = 3), hasMore = false)

        val summary = engine().run()

        assertEquals(listOf(0L, 2L), api.pullSince)
        assertEquals(3, summary.pulled)
        assertEquals(3L, db.voterDao().cursor())
        assertNotNull(db.voterDao().get(VOTER_3))
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
        api.pages += page(Fixtures.dto(VOTER_1, firstName = "ServerOlder", updatedAt = T2, revision = 7), hasMore = false)

        engine().run()

        val local = db.voterDao().get(VOTER_1)!!
        assertEquals("LocalNewer", local.firstName)
        assertTrue(local.dirty)
        assertEquals(7L, local.revision)
    }

    @Test
    fun `a pulled tombstone removes the voter from search without resurrecting it`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(dirty = false))
        api.pages += page(Fixtures.tombstoneDto(VOTER_1, revision = 9), hasMore = false)

        engine().run()

        val stored = db.voterDao().get(VOTER_1)!!
        assertNotNull(stored.deletedAt)
        assertEquals("", stored.firstName)
        assertTrue(db.voterDao().search("%", 10).first().isEmpty())
    }

    @Test
    fun `a push result is matched to its row whatever the case of the voter id`() = runBlocking {
        db.voterDao().upsert(Fixtures.entity(VOTER_1, dirty = true, updatedAt = T2, revision = 0))
        api.onPush = {
            PushResponse(
                FakeSyncApi.SERVER_TIME, 3,
                listOf(PushResult(VOTER_1.lowercase(), "applied", serverRecord = Fixtures.dto(VOTER_1, updatedAt = T2, revision = 3))),
            )
        }

        val summary = engine().run()

        assertEquals(1, summary.applied)
        val stored = db.voterDao().get(VOTER_1)!!
        assertFalse(stored.dirty)
        assertEquals(3L, stored.revision)
    }

    @Test
    fun `a revived voter id is pushed with the tombstone revision and clears the tombstone`() = runBlocking {
        val tombstone = Fixtures.entity(VOTER_1, revision = 7).copy(
            firstName = "", lastName = null, houseNo = null, street = null, mobile = null, age = null, deletedAt = T2,
        )
        db.voterDao().upsert(tombstone)
        db.voterDao().upsert(Fixtures.entity(VOTER_1, firstName = "Revived", dirty = true, updatedAt = T3, revision = 7))
        api.onPush = {
            PushResponse(
                FakeSyncApi.SERVER_TIME, 8,
                listOf(PushResult(VOTER_1, "applied", serverRecord = Fixtures.dto(VOTER_1, firstName = "Revived", updatedAt = T3, revision = 8))),
            )
        }

        engine().run()

        assertEquals(7L, api.pushRequests.single().changes.single().baseRevision)
        val stored = db.voterDao().get(VOTER_1)!!
        assertNull(stored.deletedAt)
        assertEquals("Revived", stored.firstName)
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

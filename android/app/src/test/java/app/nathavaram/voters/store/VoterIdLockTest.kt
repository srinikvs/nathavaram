package app.nathavaram.voters.store

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import app.nathavaram.voters.Fixtures
import app.nathavaram.voters.domain.FieldKey
import app.nathavaram.voters.domain.VoterForm
import app.nathavaram.voters.sync.ConflictDiff
import app.nathavaram.voters.sync.SyncMerge
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** VoterId is the unique key: typed once on add, never changed afterwards. Synthetic data only. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = android.app.Application::class)
class VoterIdLockTest {
    private lateinit var db: AppDatabase
    private lateinit var repo: VoterRepository
    private var clock = 0
    private var nextId = 0

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        repo = VoterRepository(
            db,
            now = { "2026-11-11T10:00:%02d.000000Z".format(++clock) },
            newId = { "00000000-0000-4000-8000-%012d".format(++nextId) },
        )
    }

    @After
    fun tearDown() {
        db.close()
    }

    private fun form(voterId: String = "ZZZ0000001", first: String = "Testperson") =
        VoterForm(voterId = voterId, firstName = first, gender = "Male", mobile = "9000000001", booth = "12A", ward = "3")

    @Test
    fun `editing keeps the stored voter id even when the form carries a different one`() = runBlocking {
        val id = (repo.save(null, form()) as SaveResult.Saved).id

        val result = repo.save(id, form(voterId = "ZZZ0000099", first = "Renamed"))

        assertTrue(result is SaveResult.Saved)
        val stored = repo.get(id)!!
        assertEquals("ZZZ0000001", stored.voterId)
        assertEquals("Renamed", stored.firstName)
    }

    @Test
    fun `a pushed change never carries a changed voter id`() = runBlocking {
        val id = (repo.save(null, form()) as SaveResult.Saved).id
        repo.save(id, form(voterId = "ZZZ0000099"))

        val push = SyncMerge.toPushChange(db.voterDao().get(id)!!)

        assertEquals("ZZZ0000001", push.voterId)
    }

    @Test
    fun `other fields stay editable`() = runBlocking {
        val id = (repo.save(null, form()) as SaveResult.Saved).id

        repo.save(id, form().copy(booth = "40", ward = "9", surname = "Changed"))

        val stored = repo.get(id)!!
        assertEquals("40", stored.booth)
        assertEquals("9", stored.ward)
        assertEquals("Changed", stored.surname)
    }

    @Test
    fun `adding a duplicate voter id returns a message that names the id and says it must be unique`() = runBlocking {
        repo.save(null, form())

        val result = repo.save(null, form(first = "Someone Else"))

        val message = (result as SaveResult.Invalid).errors.getValue(FieldKey.VOTER_ID)
        assertTrue(message, "ZZZ0000001" in message)
        assertTrue(message, "already exists" in message)
        assertTrue(message, "unique" in message)
        assertEquals(1, db.voterDao().dirtyRows().size)
    }

    @Test
    fun `a voter id freed by a removed record can be added again`() = runBlocking {
        db.voterDao().upsert(SyncMerge.fromDto(Fixtures.tombstoneDto(id = Fixtures.ID_1)))

        val result = repo.save(null, form())

        assertTrue(result is SaveResult.Saved)
    }

    @Test
    fun `conflict screen labels say Booth and Ward`() {
        val local = Fixtures.dto().copy(booth = "1", ward = "2")
        val server = Fixtures.dto().copy(booth = "7", ward = "8")

        val labels = ConflictDiff.diff(local, server).map { it.label }

        assertTrue("Booth" in labels)
        assertTrue("Ward" in labels)
        assertFalse(labels.any { "2024 Booth" == it || "2026 Ward" == it })
    }
}

package app.nathavaram.voters.store

import androidx.room.Room
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.core.app.ApplicationProvider
import app.nathavaram.voters.domain.FieldKey
import app.nathavaram.voters.domain.VoterForm
import app.nathavaram.voters.sync.SyncMerge
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Role rules on the device, the House No fold in the Room migration, and the new columns.
 * Synthetic data only.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = android.app.Application::class)
class FieldPermissionTest {
    private lateinit var db: AppDatabase
    private var clock = 0
    private var nextId = 0

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
    }

    @After
    fun tearDown() {
        db.close()
    }

    private fun repo(canEditAll: Boolean) = VoterRepository(
        db,
        now = { "2026-11-11T10:00:%02d.000000Z".format(++clock) },
        newId = { "00000000-0000-4000-8000-%012d".format(++nextId) },
        canEditAllFields = { canEditAll },
    )

    private fun full() = VoterForm(
        voterId = "ZZZ0000001", firstName = "Testperson", surname = "One", gender = "Male",
        booth = "12A", ward = "3", address = "H.No 12", village = "Samplevillage",
        permanentAddress = "Sample Lane 5", isLocal = "Yes", caste = "SampleGroupA",
        mobile = "9000000001", party = "TDP",
    )

    private fun createdByAdmin(): String = runBlocking {
        (repo(canEditAll = true).save(null, full()) as SaveResult.Saved).id
    }

    @Test
    fun `user can change party caste mobile and address`() = runBlocking {
        val id = createdByAdmin()

        val result = repo(canEditAll = false).save(
            id,
            full().copy(party = "BJP", caste = "SampleGroupZ", mobile = "9000000077", address = "New Address 5"),
        )

        assertTrue(result is SaveResult.Saved)
        val stored = db.voterDao().get(id)!!
        assertEquals("BJP", stored.party)
        assertEquals("SampleGroupZ", stored.caste)
        assertEquals("9000000077", stored.mobile)
        assertEquals("New Address 5", stored.address)
        assertTrue(stored.dirty)
    }

    @Test
    fun `user edit never changes a restricted field or the voter id`() = runBlocking {
        val id = createdByAdmin()
        val tampered = full().copy(
            voterId = "ZZZ9999999", firstName = "Hacked", surname = "Changed", booth = "99", ward = "9",
            village = "Othervillage", permanentAddress = "Other Lane", isLocal = "No", gender = "Female",
            age = "50", cCity = "Elsewhere", profession = "Driver", caste = "SampleGroupZ",
        )

        repo(canEditAll = false).save(id, tampered)

        val stored = db.voterDao().get(id)!!
        assertEquals("SampleGroupZ", stored.caste) // the allowed change still goes through
        assertEquals("ZZZ0000001", stored.voterId)
        assertEquals("Testperson", stored.firstName)
        assertEquals("One", stored.surname)
        assertEquals("12A", stored.booth)
        assertEquals("3", stored.ward)
        assertEquals("Samplevillage", stored.village)
        assertEquals("Sample Lane 5", stored.permanentAddress)
        assertTrue(stored.isLocal)
        assertEquals("Male", stored.gender)
        assertNull(stored.age)
        assertNull(stored.cCity)
        assertNull(stored.profession)
    }

    @Test
    fun `the sync payload of a user edit carries no restricted change`() = runBlocking {
        val id = createdByAdmin()
        val before = SyncMerge.toPushChange(db.voterDao().get(id)!!)

        repo(canEditAll = false).save(id, full().copy(firstName = "Hacked", ward = "9", party = "INC"))

        val push = SyncMerge.toPushChange(db.voterDao().get(id)!!)
        assertEquals(before.copy(updatedAt = push.updatedAt, party = "INC"), push)
    }

    @Test
    fun `owner or admin can change every field except the voter id`() = runBlocking {
        val id = createdByAdmin()

        repo(canEditAll = true).save(
            id,
            full().copy(
                voterId = "ZZZ9999999", firstName = "Renamed", ward = "9", village = "Newvillage",
                permanentAddress = "New Lane", isLocal = "No",
            ),
        )

        val stored = db.voterDao().get(id)!!
        assertEquals("ZZZ0000001", stored.voterId)
        assertEquals("Renamed", stored.firstName)
        assertEquals("9", stored.ward)
        assertEquals("Newvillage", stored.village)
        assertEquals("New Lane", stored.permanentAddress)
        assertFalse(stored.isLocal)
    }

    @Test
    fun `a user can still add a new voter with every field`() = runBlocking {
        val id = (repo(canEditAll = false).save(null, full()) as SaveResult.Saved).id

        val stored = db.voterDao().get(id)!!
        assertEquals("Testperson", stored.firstName)
        assertEquals("Samplevillage", stored.village)
        assertEquals("Sample Lane 5", stored.permanentAddress)
        assertTrue(stored.isLocal)
    }

    @Test
    fun `an invalid local value is rejected`() = runBlocking {
        val result = repo(canEditAll = true).save(null, full().copy(isLocal = "maybe"))

        assertTrue(FieldKey.IS_LOCAL in (result as SaveResult.Invalid).errors)
    }

    @Test
    fun `local phone search finds a voter by village or address`() = runBlocking {
        val repository = repo(canEditAll = true)
        repository.save(null, full())

        assertEquals(1, repository.search("samplevil").first().size)
        assertEquals(1, repository.search("H.No 12").first().size)
        assertEquals(0, repository.search("nowhere").first().size)
    }

    // --- Room migration v1 -> v2 -------------------------------------------------

    private fun openV1(): SupportSQLiteDatabase {
        val config = SupportSQLiteOpenHelper.Configuration.builder(ApplicationProvider.getApplicationContext())
            .name(null) // in-memory
            .callback(object : SupportSQLiteOpenHelper.Callback(1) {
                override fun onCreate(db: SupportSQLiteDatabase) {
                    db.execSQL(V1_VOTERS)
                }

                override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
            })
            .build()
        return FrameworkSQLiteOpenHelperFactory().create(config).writableDatabase
    }

    private fun insertV1(db: SupportSQLiteDatabase, n: Int, houseNo: String?, dirty: Int = 0, deletedAt: String? = null) {
        db.execSQL(
            "INSERT INTO voters (id, booth_2024, ward_2026, voter_id, first_name, house_no, gender, mobile," +
                " created_at, updated_at, revision, deleted_at, dirty) VALUES (?, '1', '2', ?, 'Fakeperson'," +
                " ?, 'Male', '9000000001', 't1', 't2', ?, ?, ?)",
            arrayOf<Any?>("id-$n", "ZZZ800000$n", houseNo, n, deletedAt, dirty),
        )
    }

    @Test
    fun `migration folds house no into address drops the column and keeps every row`() {
        val v1 = openV1()
        insertV1(v1, 1, "12")
        insertV1(v1, 2, "12-A", dirty = 1)
        insertV1(v1, 3, "")
        insertV1(v1, 4, null)
        insertV1(v1, 5, "  7 ", deletedAt = "t3")

        Migrations.migrateV1ToV2(v1)

        val columns = v1.query("PRAGMA table_info(voters)").use { c ->
            generateSequence { if (c.moveToNext()) c.getString(c.getColumnIndexOrThrow("name")) else null }.toList()
        }
        assertFalse("house_no" in columns)
        assertTrue(columns.containsAll(listOf("address", "village", "permanent_address", "is_local")))
        val rows = v1.query(
            "SELECT voter_id, address, village, permanent_address, is_local, dirty, booth_2024, deleted_at" +
                " FROM voters ORDER BY revision",
        ).use { c ->
            generateSequence {
                if (!c.moveToNext()) null
                else listOf(
                    c.getString(0), c.getString(1), c.getString(2), c.getString(3),
                    c.getInt(4), c.getInt(5), c.getString(6), c.getString(7),
                )
            }.toList()
        }
        assertEquals(5, rows.size)
        assertEquals("H.No 12", rows[0][1])
        assertEquals("H.No 12-A", rows[1][1])
        assertNull(rows[2][1])
        assertNull(rows[3][1])
        assertEquals("H.No 7", rows[4][1])
        assertTrue(rows.all { it[2] == null && it[3] == null && it[4] == 0 && it[6] == "1" })
        assertEquals(1, rows[1][5]) // a dirty row stays dirty
        assertEquals("t3", rows[4][7]) // a tombstone stays a tombstone
        v1.close()
    }

    private companion object {
        /** The v1 voters table exactly as Room created it before the House No removal. */
        const val V1_VOTERS = """
            CREATE TABLE `voters` (
                `id` TEXT NOT NULL, `booth_2024` TEXT, `bs_no_2024` TEXT, `ward_2026` TEXT, `ward_s_no` TEXT,
                `voter_id` TEXT NOT NULL, `first_name` TEXT NOT NULL, `surname` TEXT, `relative_name` TEXT,
                `house_no` TEXT, `gender` TEXT NOT NULL, `age` INTEGER, `caste` TEXT, `mobile` TEXT,
                `c_city` TEXT, `profession` TEXT, `party` TEXT, `created_at` TEXT NOT NULL,
                `updated_at` TEXT NOT NULL, `revision` INTEGER NOT NULL, `deleted_at` TEXT, `source_id` TEXT,
                `dirty` INTEGER NOT NULL, PRIMARY KEY(`id`)
            )
        """
    }
}

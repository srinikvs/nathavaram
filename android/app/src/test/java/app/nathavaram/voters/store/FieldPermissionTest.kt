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
 * Role rules on the device, the Room migrations (v1 -> v2 -> v3), and the new columns.
 * Synthetic data only.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = android.app.Application::class)
class FieldPermissionTest {
    private lateinit var db: AppDatabase
    private var clock = 0

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
        canEditAllFields = { canEditAll },
    )

    private fun full() = VoterForm(
        voterId = "ZZZ0000001", firstName = "Testperson", lastName = "One",
        booth = "12A", ward = "3", houseNo = "12", street = "Sample Street", village = "Samplevillage",
        panchayath = "Samplepanchayath", mandal = "Samplemandal", isLocal = "Local",
        caste = "SampleGroupA", mobile = "9000000001", party = "TDP",
    )

    private fun createdByAdmin(): String = runBlocking {
        (repo(canEditAll = true).save(null, full()) as SaveResult.Saved).voterId
    }

    @Test
    fun `user can change the fourteen product fields`() = runBlocking {
        val id = createdByAdmin()

        val result = repo(canEditAll = false).save(
            id,
            full().copy(
                party = "BJP", mobile = "9000000077", houseNo = "99-Z", street = "New Street 5",
                village = "Othervillage", currentCity = "Elsewhere", age = "50", isLocal = "Non-Local",
            ),
        )

        assertTrue(result is SaveResult.Saved)
        val stored = db.voterDao().get(id)!!
        assertEquals("BJP", stored.party)
        assertEquals("9000000077", stored.mobile)
        assertEquals("99-Z", stored.houseNo)
        assertEquals("New Street 5", stored.street)
        assertEquals("Othervillage", stored.village)
        assertEquals("Elsewhere", stored.currentCity)
        assertEquals(50, stored.age)
        assertFalse(stored.isLocal)
        assertTrue(stored.dirty)
    }

    @Test
    fun `the editable fields are exactly the fourteen product fields`() {
        assertEquals(
            setOf(
                "booth", "ward", "booth_s_no", "ward_s_no", "relative_name", "house_no", "age", "street",
                "mobile", "village", "panchayath", "current_city", "is_local", "party",
            ),
            FieldKey.USER_EDITABLE,
        )
    }

    @Test
    fun `user edit never changes a locked field or the voter id`() = runBlocking {
        val id = createdByAdmin()
        val tampered = full().copy(
            voterId = "ZZZ9999999", firstName = "Hacked", lastName = "Changed", mandal = "Othermandal",
            constituency = "Otherconstituency", caste = "SampleGroupZ",
        )

        repo(canEditAll = false).save(id, tampered)

        val stored = db.voterDao().get(id)!!
        assertEquals("ZZZ0000001", stored.voterId)
        assertNull(db.voterDao().get("ZZZ9999999"))
        assertEquals("Testperson", stored.firstName)
        assertEquals("One", stored.lastName)
        assertEquals("SampleGroupA", stored.caste)
        assertEquals("Samplemandal", stored.mandal)
        assertFalse("Otherconstituency" == stored.constituency)
    }

    @Test
    fun `the sync payload of a user edit carries no restricted change`() = runBlocking {
        val id = createdByAdmin()
        val before = SyncMerge.toPushChange(db.voterDao().get(id)!!)

        repo(canEditAll = false).save(id, full().copy(firstName = "Hacked", caste = "SampleGroupZ", party = "INC"))

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
                panchayath = "Newpanchayath", isLocal = "Non-Local",
            ),
        )

        val stored = db.voterDao().get(id)!!
        assertEquals("ZZZ0000001", stored.voterId)
        assertEquals("Renamed", stored.firstName)
        assertEquals("9", stored.ward)
        assertEquals("Newvillage", stored.village)
        assertEquals("Newpanchayath", stored.panchayath)
        assertFalse(stored.isLocal)
    }

    @Test
    fun `a user can still add a new voter with every field`() = runBlocking {
        val id = (repo(canEditAll = false).save(null, full()) as SaveResult.Saved).voterId

        val stored = db.voterDao().get(id)!!
        assertEquals("Testperson", stored.firstName)
        assertEquals("Samplevillage", stored.village)
        assertEquals("Samplemandal", stored.mandal)
        assertTrue(stored.isLocal)
    }

    @Test
    fun `an invalid local value is rejected`() = runBlocking {
        val result = repo(canEditAll = true).save(null, full().copy(isLocal = "maybe"))

        assertTrue(FieldKey.IS_LOCAL in (result as SaveResult.Invalid).errors)
    }

    @Test
    fun `local phone search finds a voter by village street house no panchayath or mandal`() = runBlocking {
        val repository = repo(canEditAll = true)
        repository.save(null, full())

        assertEquals(1, repository.search("samplevil").first().size)
        assertEquals(1, repository.search("Sample Street").first().size)
        assertEquals(1, repository.search("12").first().size)
        assertEquals(1, repository.search("samplepanch").first().size)
        assertEquals(1, repository.search("samplemand").first().size)
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

    // --- Room migration v2 -> v3 (voter_id becomes the primary key) --------------

    private fun columnsOf(db: SupportSQLiteDatabase, table: String): List<String> =
        db.query("PRAGMA table_info($table)").use { c ->
            generateSequence { if (c.moveToNext()) c.getString(c.getColumnIndexOrThrow("name")) else null }.toList()
        }

    private fun primaryKeyOf(db: SupportSQLiteDatabase, table: String): List<String> =
        db.query("PRAGMA table_info($table)").use { c ->
            generateSequence {
                if (c.moveToNext()) c.getString(c.getColumnIndexOrThrow("name")) to c.getInt(c.getColumnIndexOrThrow("pk"))
                else null
            }.filter { it.second > 0 }.map { it.first }.toList()
        }

    private fun v2Database(): SupportSQLiteDatabase {
        val v1 = openV1()
        v1.execSQL(
            "CREATE TABLE `conflicts` (`voter_uuid` TEXT NOT NULL, `kind` TEXT NOT NULL, `message` TEXT," +
                " `detected_at` TEXT NOT NULL, `local_json` TEXT, `server_json` TEXT, PRIMARY KEY(`voter_uuid`))",
        )
        return v1
    }

    private fun insertV1Row(
        db: SupportSQLiteDatabase, n: Int, voterId: String, houseNo: String?, updatedAt: String = "t2",
        dirty: Int = 0, deletedAt: String? = null,
    ) {
        db.execSQL(
            "INSERT INTO voters (id, booth_2024, bs_no_2024, ward_2026, voter_id, first_name, surname, house_no," +
                " gender, age, c_city, profession, mobile, created_at, updated_at, revision, deleted_at, dirty)" +
                " VALUES (?, '1', '101', '2', ?, 'Fakeperson', 'Surn', ?, 'Male', 40, 'Hyd', 'Farmer'," +
                " '9000000001', 't1', ?, ?, ?, ?)",
            arrayOf<Any?>("id-$n", voterId, houseNo, updatedAt, n, deletedAt, dirty),
        )
    }

    @Test
    fun `migration to v3 makes voter_id the primary key and renames the columns`() {
        val old = v2Database()
        insertV1Row(old, 1, "ZZZ8000001", "12")
        Migrations.migrateV1ToV2(old)
        old.execSQL("UPDATE voters SET address = address || ', Main St'")

        Migrations.migrateV2ToV3(old)

        assertEquals(listOf("voter_id"), primaryKeyOf(old, "voters"))
        val columns = columnsOf(old, "voters")
        assertFalse("id" in columns)
        assertTrue(columns.containsAll(listOf("booth_s_no", "last_name", "house_no", "street", "panchayath", "mandal", "current_city")))
        assertTrue(columns.none { it in setOf("gender", "permanent_address", "profession", "address", "surname", "c_city", "bs_no_2024") })
        old.query("SELECT voter_id, booth, booth_s_no, ward, last_name, house_no, street, current_city, age, mobile, is_local FROM voters").use { c ->
            assertTrue(c.moveToFirst())
            assertEquals("ZZZ8000001", c.getString(0))
            assertEquals("1", c.getString(1))
            assertEquals("101", c.getString(2))
            assertEquals("2", c.getString(3))
            assertEquals("Surn", c.getString(4))
            assertEquals("12", c.getString(5))
            assertEquals("Main St", c.getString(6))
            assertEquals("Hyd", c.getString(7))
            assertEquals(40, c.getInt(8))
            assertEquals("9000000001", c.getString(9))
            assertEquals(0, c.getInt(10))
        }
        old.close()
    }

    @Test
    fun `migration to v3 keeps dirty rows revisions and tombstones`() {
        val old = v2Database()
        insertV1Row(old, 1, "ZZZ8000001", null, dirty = 1)
        insertV1Row(old, 2, "00000000000000000000000000000002", null, deletedAt = "t3")
        Migrations.migrateV1ToV2(old)

        Migrations.migrateV2ToV3(old)

        old.query("SELECT voter_id, dirty, revision, deleted_at, street, house_no FROM voters ORDER BY revision").use { c ->
            assertTrue(c.moveToFirst())
            assertEquals(1, c.getInt(1)) // a dirty row stays dirty, so its edit still syncs
            assertEquals(1, c.getInt(2))
            assertTrue(c.moveToNext())
            assertEquals("00000000000000000000000000000002", c.getString(0)) // the tombstone keeps its key
            assertEquals("t3", c.getString(3))
            assertNull(c.getString(4))
            assertNull(c.getString(5))
            assertFalse(c.moveToNext())
        }
        old.close()
    }

    @Test
    fun `migration to v3 keeps the newest row when a voter id repeats`() {
        val old = v2Database()
        insertV1Row(old, 1, "ZZZ8000001", null, updatedAt = "2026-11-11T09:00:00.000000Z")
        insertV1Row(old, 2, "ZZZ8000001", null, updatedAt = "2026-11-11T10:00:00.000000Z")
        Migrations.migrateV1ToV2(old)

        Migrations.migrateV2ToV3(old)

        old.query("SELECT COUNT(*), MAX(revision) FROM voters").use { c ->
            assertTrue(c.moveToFirst())
            assertEquals(1, c.getInt(0))
            assertEquals(2, c.getInt(1)) // the later row survived
        }
        old.close()
    }

    @Test
    fun `migration to v3 rebuilds the conflicts table keyed on voter_id and clears old notes`() {
        val old = v2Database()
        insertV1Row(old, 1, "ZZZ8000001", null)
        old.execSQL("INSERT INTO conflicts VALUES ('id-1', 'conflict', 'old', 't', '{}', '{}')")
        Migrations.migrateV1ToV2(old)

        Migrations.migrateV2ToV3(old)

        assertEquals(listOf("voter_id"), primaryKeyOf(old, "conflicts"))
        old.query("SELECT COUNT(*) FROM conflicts").use { c ->
            assertTrue(c.moveToFirst())
            assertEquals(0, c.getInt(0))
        }
        old.close()
    }

    @Test
    fun `split house no keeps long or wordy text whole in street`() {
        assertEquals("12" to "Main St", Migrations.splitHouseNo("H.No 12, Main St"))
        assertEquals("12" to null, Migrations.splitHouseNo("H.No 12"))
        assertEquals(null to "Temple Road", Migrations.splitHouseNo("Temple Road"))
        assertEquals(null to null, Migrations.splitHouseNo(null))
        val wordy = "H.No 12 near the old temple opposite the primary school"
        assertEquals(null to wordy, Migrations.splitHouseNo(wordy))
        val longNumber = "H.No " + "9".repeat(70) + ", Main St"
        assertEquals(null to longNumber, Migrations.splitHouseNo(longNumber))
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

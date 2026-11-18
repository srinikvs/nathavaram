package app.nathavaram.voters.store

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/** Room migrations for the local database. */
object Migrations {
    /**
     * v1 -> v2. Mirrors the server migrations 0005 and 0006:
     *  - `house_no` is folded into the start of the new `address` column, then dropped:
     *    "12" -> "H.No 12" (the v1 table had no address, so there is nothing to append to);
     *    an empty house_no leaves address NULL.
     *  - `village`, `permanent_address` are added as nullable text; `is_local` as NOT NULL DEFAULT 0 (No).
     *
     * The table is rebuilt (create, copy, drop, rename) because `ALTER TABLE ... DROP COLUMN` is not
     * available on older Android SQLite versions. Every row, including dirty and soft-deleted ones,
     * is copied unchanged; the local `booth_2024` / `ward_2026` column names are kept.
     */
    val MIGRATION_1_2: Migration = object : Migration(1, 2) {
        override fun migrate(db: SupportSQLiteDatabase) {
            migrateV1ToV2(db)
        }
    }

    internal fun migrateV1ToV2(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE `voters_new` (
                `id` TEXT NOT NULL,
                `booth_2024` TEXT,
                `bs_no_2024` TEXT,
                `ward_2026` TEXT,
                `ward_s_no` TEXT,
                `voter_id` TEXT NOT NULL,
                `first_name` TEXT NOT NULL,
                `surname` TEXT,
                `relative_name` TEXT,
                `address` TEXT,
                `village` TEXT,
                `permanent_address` TEXT,
                `is_local` INTEGER NOT NULL DEFAULT 0,
                `gender` TEXT NOT NULL,
                `age` INTEGER,
                `caste` TEXT,
                `mobile` TEXT,
                `c_city` TEXT,
                `profession` TEXT,
                `party` TEXT,
                `created_at` TEXT NOT NULL,
                `updated_at` TEXT NOT NULL,
                `revision` INTEGER NOT NULL,
                `deleted_at` TEXT,
                `source_id` TEXT,
                `dirty` INTEGER NOT NULL,
                PRIMARY KEY(`id`)
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            INSERT INTO `voters_new` (
                `id`, `booth_2024`, `bs_no_2024`, `ward_2026`, `ward_s_no`, `voter_id`, `first_name`,
                `surname`, `relative_name`, `address`, `gender`, `age`, `caste`, `mobile`, `c_city`,
                `profession`, `party`, `created_at`, `updated_at`, `revision`, `deleted_at`,
                `source_id`, `dirty`
            )
            SELECT
                `id`, `booth_2024`, `bs_no_2024`, `ward_2026`, `ward_s_no`, `voter_id`, `first_name`,
                `surname`, `relative_name`,
                CASE WHEN `house_no` IS NULL OR TRIM(`house_no`) = '' THEN NULL
                     ELSE 'H.No ' || TRIM(`house_no`) END,
                `gender`, `age`, `caste`, `mobile`, `c_city`,
                `profession`, `party`, `created_at`, `updated_at`, `revision`, `deleted_at`,
                `source_id`, `dirty`
            FROM `voters`
            """.trimIndent(),
        )
        db.execSQL("DROP TABLE `voters`")
        db.execSQL("ALTER TABLE `voters_new` RENAME TO `voters`")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_voters_voter_id` ON `voters` (`voter_id`)")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_voters_dirty` ON `voters` (`dirty`)")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_voters_deleted_at` ON `voters` (`deleted_at`)")
    }

    /**
     * v2 -> v3. Mirrors server migration 0007: `voter_id` becomes the primary key and the columns
     * follow the new voter list.
     *
     *  - `id` is removed; `bs_no_2024` -> `booth_s_no`, `surname` -> `last_name`,
     *    `c_city` -> `current_city`, `booth_2024` / `ward_2026` -> `booth` / `ward`.
     *  - `address` becomes `street`, with a leading "H.No <n>," split out into `house_no`
     *    (see [splitHouseNo]). `panchayath` and `mandal` are added empty.
     *  - `gender`, `permanent_address` and `profession` are dropped; their values are not kept.
     *  - Soft-deleted rows keep the placeholder `voter_id` they already had as their key.
     *  - If two rows ever shared a `voter_id`, the one with the newest `updated_at` is kept.
     *  - Every row keeps its `dirty` flag, `revision` and timestamps, so pending edits still sync.
     *  - The `conflicts` notes are cleared: they were stored as old-shape JSON and keyed by the old
     *    row id, and they only describe edits the server already settled.
     *
     * The tables are rebuilt (create, copy, drop, rename) because `ALTER TABLE ... DROP COLUMN` is
     * not available on older Android SQLite versions.
     */
    val MIGRATION_2_3: Migration = object : Migration(2, 3) {
        override fun migrate(db: SupportSQLiteDatabase) {
            migrateV2ToV3(db)
        }
    }

    private const val HOUSE_PREFIX = "H.No "
    private const val HOUSE_NO_MAX = 64

    /**
     * "H.No 12, Main St" -> ("12", "Main St"); "H.No 12" -> ("12", null); anything else, or a
     * house number that is longer than 64 characters or has spaces, stays whole in street.
     */
    internal fun splitHouseNo(address: String?): Pair<String?, String?> {
        if (address == null || !address.startsWith(HOUSE_PREFIX)) return null to address
        val remainder = address.substring(HOUSE_PREFIX.length)
        val comma = remainder.indexOf(", ")
        val house = (if (comma >= 0) remainder.substring(0, comma) else remainder).trim()
        if (house.isEmpty() || house.length > HOUSE_NO_MAX || house.any { it.isWhitespace() }) {
            return null to address
        }
        val rest = if (comma >= 0) remainder.substring(comma + 2).trim().ifEmpty { null } else null
        return house to rest
    }

    internal fun migrateV2ToV3(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE `voters_new` (
                `voter_id` TEXT NOT NULL,
                `booth` TEXT,
                `booth_s_no` TEXT,
                `ward` TEXT,
                `ward_s_no` TEXT,
                `first_name` TEXT NOT NULL,
                `last_name` TEXT,
                `relative_name` TEXT,
                `house_no` TEXT,
                `age` INTEGER,
                `street` TEXT,
                `mobile` TEXT,
                `caste` TEXT,
                `village` TEXT,
                `panchayath` TEXT,
                `mandal` TEXT,
                `current_city` TEXT,
                `is_local` INTEGER NOT NULL DEFAULT 0,
                `party` TEXT,
                `created_at` TEXT NOT NULL,
                `updated_at` TEXT NOT NULL,
                `revision` INTEGER NOT NULL,
                `deleted_at` TEXT,
                `source_id` TEXT,
                `dirty` INTEGER NOT NULL,
                PRIMARY KEY(`voter_id`)
            )
            """.trimIndent(),
        )
        // Oldest first, so INSERT OR REPLACE leaves the newest row if a voter_id repeats.
        db.query("SELECT * FROM `voters` ORDER BY `updated_at` ASC").use { c ->
            fun text(name: String): String? = c.getColumnIndexOrThrow(name).let { if (c.isNull(it)) null else c.getString(it) }
            fun long(name: String): Long = c.getLong(c.getColumnIndexOrThrow(name))
            while (c.moveToNext()) {
                val active = text("deleted_at") == null
                val (house, street) = if (active) splitHouseNo(text("address")) else (null to null)
                val age = c.getColumnIndexOrThrow("age").let { if (c.isNull(it)) null else c.getLong(it) }
                db.execSQL(
                    """
                    INSERT OR REPLACE INTO `voters_new` (
                        `voter_id`, `booth`, `booth_s_no`, `ward`, `ward_s_no`, `first_name`, `last_name`,
                        `relative_name`, `house_no`, `age`, `street`, `mobile`, `caste`, `village`,
                        `panchayath`, `mandal`, `current_city`, `is_local`, `party`, `created_at`,
                        `updated_at`, `revision`, `deleted_at`, `source_id`, `dirty`
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """.trimIndent(),
                    arrayOf<Any?>(
                        text("voter_id"), text("booth_2024"), text("bs_no_2024"), text("ward_2026"),
                        text("ward_s_no"), text("first_name"), text("surname"), text("relative_name"),
                        house, age, street, text("mobile"), text("caste"), text("village"),
                        null, null, text("c_city"), long("is_local"), text("party"),
                        text("created_at"), text("updated_at"), long("revision"), text("deleted_at"),
                        text("source_id"), long("dirty"),
                    ),
                )
            }
        }
        db.execSQL("DROP TABLE `voters`")
        db.execSQL("ALTER TABLE `voters_new` RENAME TO `voters`")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_voters_dirty` ON `voters` (`dirty`)")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_voters_deleted_at` ON `voters` (`deleted_at`)")

        db.execSQL("DROP TABLE `conflicts`")
        db.execSQL(
            """
            CREATE TABLE `conflicts` (
                `voter_id` TEXT NOT NULL,
                `kind` TEXT NOT NULL,
                `message` TEXT,
                `detected_at` TEXT NOT NULL,
                `local_json` TEXT,
                `server_json` TEXT,
                PRIMARY KEY(`voter_id`)
            )
            """.trimIndent(),
        )
    }
}

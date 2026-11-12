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
}

package app.nathavaram.voters.store

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(
    entities = [VoterEntity::class, ConflictEntity::class, SyncStateEntity::class],
    version = 4,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun voterDao(): VoterDao
    abstract fun conflictDao(): ConflictDao
}

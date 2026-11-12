package app.nathavaram.voters.store

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface VoterDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(voter: VoterEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(voters: List<VoterEntity>)

    @Query("SELECT * FROM voters WHERE id = :id")
    suspend fun get(id: String): VoterEntity?

    @Query("DELETE FROM voters WHERE id = :id")
    suspend fun delete(id: String)

    /** Another active (not soft-deleted) row that already uses [voterId], if any. */
    @Query("SELECT * FROM voters WHERE voter_id = :voterId AND deleted_at IS NULL AND id != :excludeId LIMIT 1")
    suspend fun activeWithVoterId(voterId: String, excludeId: String): VoterEntity?

    @Query("SELECT * FROM voters WHERE dirty = 1 ORDER BY updated_at")
    suspend fun dirtyRows(): List<VoterEntity>

    @Query("SELECT COUNT(*) FROM voters WHERE dirty = 1")
    fun dirtyCount(): Flow<Int>

    /** [pattern] is a LIKE pattern using `\` as the escape character. Soft-deleted rows are skipped. */
    @Query(
        """
        SELECT * FROM voters
        WHERE deleted_at IS NULL AND (
            voter_id LIKE :pattern ESCAPE '\' OR first_name LIKE :pattern ESCAPE '\' OR
            surname LIKE :pattern ESCAPE '\' OR relative_name LIKE :pattern ESCAPE '\' OR
            mobile LIKE :pattern ESCAPE '\' OR address LIKE :pattern ESCAPE '\' OR
            village LIKE :pattern ESCAPE '\'
        )
        ORDER BY first_name COLLATE NOCASE, voter_id
        LIMIT :limit
        """,
    )
    fun search(pattern: String, limit: Int): Flow<List<VoterEntity>>

    @Query("SELECT server_revision FROM sync_state WHERE id = 1")
    suspend fun cursor(): Long?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putState(state: SyncStateEntity)
}

@Dao
interface ConflictDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(conflict: ConflictEntity)

    @Query("SELECT * FROM conflicts WHERE voter_uuid = :id")
    suspend fun get(id: String): ConflictEntity?

    @Query("SELECT * FROM conflicts ORDER BY detected_at DESC")
    fun all(): Flow<List<ConflictEntity>>

    @Query("DELETE FROM conflicts WHERE voter_uuid = :id")
    suspend fun delete(id: String)
}

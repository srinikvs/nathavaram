package app.nathavaram.voters.store

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/** One voter row: every docs/SCHEMA.md column, plus the local-only [dirty] flag. */
@Entity(
    tableName = "voters",
    indices = [Index("voter_id"), Index("dirty"), Index("deleted_at")],
)
data class VoterEntity(
    @PrimaryKey val id: String,
    @ColumnInfo(name = "booth_2024") val booth2024: String? = null,
    @ColumnInfo(name = "bs_no_2024") val bsNo2024: String? = null,
    @ColumnInfo(name = "ward_2026") val ward2026: String? = null,
    @ColumnInfo(name = "ward_s_no") val wardSNo: String? = null,
    @ColumnInfo(name = "voter_id") val voterId: String,
    @ColumnInfo(name = "first_name") val firstName: String,
    val surname: String? = null,
    @ColumnInfo(name = "relative_name") val relativeName: String? = null,
    @ColumnInfo(name = "house_no") val houseNo: String? = null,
    val gender: String = "Unknown",
    val age: Int? = null,
    val caste: String? = null,
    val mobile: String? = null,
    @ColumnInfo(name = "c_city") val cCity: String? = null,
    val profession: String? = null,
    val party: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: String,
    @ColumnInfo(name = "updated_at") val updatedAt: String,
    val revision: Long = 0,
    @ColumnInfo(name = "deleted_at") val deletedAt: String? = null,
    @ColumnInfo(name = "source_id") val sourceId: String? = null,
    /** True while the row has local edits the server has not accepted yet. */
    val dirty: Boolean = false,
)

/** A push the server did not apply, kept until the operator has looked at it. */
@Entity(tableName = "conflicts")
data class ConflictEntity(
    @PrimaryKey @ColumnInfo(name = "voter_uuid") val voterUuid: String,
    val kind: String,
    val message: String?,
    @ColumnInfo(name = "detected_at") val detectedAt: String,
    @ColumnInfo(name = "local_json") val localJson: String?,
    @ColumnInfo(name = "server_json") val serverJson: String?,
)

/** The single sync cursor row (`id = 1`). */
@Entity(tableName = "sync_state")
data class SyncStateEntity(
    @PrimaryKey val id: Int = 1,
    @ColumnInfo(name = "server_revision") val serverRevision: Long = 0,
)

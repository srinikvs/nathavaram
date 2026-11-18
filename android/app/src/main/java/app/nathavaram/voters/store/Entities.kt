package app.nathavaram.voters.store

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * One voter row: every docs/SCHEMA.md column, plus the local-only [dirty] flag. [voterId] (the
 * EPIC number) is the primary key, exactly as on the server: there is no separate row id.
 */
@Entity(
    tableName = "voters",
    indices = [Index("dirty"), Index("deleted_at")],
)
data class VoterEntity(
    @PrimaryKey @ColumnInfo(name = "voter_id") val voterId: String,
    val booth: String? = null,
    @ColumnInfo(name = "booth_s_no") val boothSNo: String? = null,
    val ward: String? = null,
    @ColumnInfo(name = "ward_s_no") val wardSNo: String? = null,
    @ColumnInfo(name = "first_name") val firstName: String,
    @ColumnInfo(name = "last_name") val lastName: String? = null,
    @ColumnInfo(name = "relative_name") val relativeName: String? = null,
    @ColumnInfo(name = "house_no") val houseNo: String? = null,
    val age: Int? = null,
    val street: String? = null,
    val mobile: String? = null,
    val caste: String? = null,
    val village: String? = null,
    val panchayath: String? = null,
    val mandal: String? = null,
    @ColumnInfo(name = "current_city") val currentCity: String? = null,
    /** Local / Non-Local. Defaults to Non-Local (false). */
    @ColumnInfo(name = "is_local", defaultValue = "0") val isLocal: Boolean = false,
    val party: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: String,
    @ColumnInfo(name = "updated_at") val updatedAt: String,
    val revision: Long = 0,
    @ColumnInfo(name = "deleted_at") val deletedAt: String? = null,
    @ColumnInfo(name = "source_id") val sourceId: String? = null,
    /** True while the row has local edits the server has not accepted yet. */
    val dirty: Boolean = false,
)

/** A push the server did not apply, kept until the operator has looked at it. One per voter. */
@Entity(tableName = "conflicts")
data class ConflictEntity(
    @PrimaryKey @ColumnInfo(name = "voter_id") val voterId: String,
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

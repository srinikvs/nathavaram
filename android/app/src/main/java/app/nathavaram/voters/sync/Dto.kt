package app.nathavaram.voters.sync

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

object SyncJson {
    val json: Json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        explicitNulls = true
    }
}

object ResultStatus {
    const val APPLIED = "applied"
    const val CONFLICT = "conflict"
    const val VOTER_ID_CONFLICT = "voter_id_conflict"
    const val VALIDATION_ERROR = "validation_error"

    /** The user role tried to change a field only an owner/admin may change (or to delete). Nothing was applied. */
    const val FORBIDDEN = "forbidden"
}

/** A voter as the server returns it (pull changes and `server_record`). */
@Serializable
data class VoterDto(
    val id: String,
    @SerialName("booth") val booth: String? = null,
    @SerialName("bs_no_2024") val bsNo2024: String? = null,
    @SerialName("ward") val ward: String? = null,
    @SerialName("ward_s_no") val wardSNo: String? = null,
    @SerialName("voter_id") val voterId: String = "",
    @SerialName("first_name") val firstName: String = "",
    val surname: String? = null,
    @SerialName("relative_name") val relativeName: String? = null,
    val address: String? = null,
    val village: String? = null,
    @SerialName("permanent_address") val permanentAddress: String? = null,
    @SerialName("is_local") val isLocal: Boolean = false,
    val gender: String = "Unknown",
    val age: Int? = null,
    val caste: String? = null,
    val mobile: String? = null,
    @SerialName("c_city") val cCity: String? = null,
    val profession: String? = null,
    val party: String? = null,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
    val revision: Long = 0,
    @SerialName("deleted_at") val deletedAt: String? = null,
    @SerialName("source_id") val sourceId: String? = null,
)

/** One full-snapshot item in `POST /api/sync/push`. */
@Serializable
data class PushChange(
    val id: String,
    @SerialName("updated_at") val updatedAt: String,
    @SerialName("created_at") val createdAt: String,
    val deleted: Boolean = false,
    @SerialName("base_revision") val baseRevision: Long? = null,
    @SerialName("booth") val booth: String? = null,
    @SerialName("bs_no_2024") val bsNo2024: String? = null,
    @SerialName("ward") val ward: String? = null,
    @SerialName("ward_s_no") val wardSNo: String? = null,
    @SerialName("voter_id") val voterId: String,
    @SerialName("first_name") val firstName: String,
    val surname: String? = null,
    @SerialName("relative_name") val relativeName: String? = null,
    val address: String? = null,
    val village: String? = null,
    @SerialName("permanent_address") val permanentAddress: String? = null,
    @SerialName("is_local") val isLocal: Boolean = false,
    val gender: String,
    val age: Int? = null,
    val caste: String? = null,
    val mobile: String? = null,
    @SerialName("c_city") val cCity: String? = null,
    val profession: String? = null,
    val party: String? = null,
)

@Serializable
data class PushRequest(
    @SerialName("device_id") val deviceId: String,
    val changes: List<PushChange>,
)

@Serializable
data class PushResult(
    val id: String,
    val status: String,
    val message: String? = null,
    @SerialName("server_revision") val serverRevision: Long? = null,
    @SerialName("server_record") val serverRecord: VoterDto? = null,
)

@Serializable
data class PushResponse(
    @SerialName("server_time") val serverTime: String = "",
    @SerialName("server_revision") val serverRevision: Long = 0,
    val results: List<PushResult> = emptyList(),
)

@Serializable
data class PullResponse(
    @SerialName("server_time") val serverTime: String = "",
    @SerialName("server_revision") val serverRevision: Long = 0,
    val since: Long = 0,
    @SerialName("has_more") val hasMore: Boolean = false,
    val changes: List<VoterDto> = emptyList(),
)

@Serializable
data class LoginRequest(val username: String, val password: String)

@Serializable
data class LoginResponse(
    @SerialName("access_token") val accessToken: String,
    val username: String = "",
    val role: String = "",
)

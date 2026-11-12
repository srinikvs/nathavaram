package app.nathavaram.voters.store

import app.nathavaram.voters.domain.FieldKey
import app.nathavaram.voters.domain.Timestamps
import app.nathavaram.voters.domain.VoterForm
import app.nathavaram.voters.domain.VoterValidator
import kotlinx.coroutines.flow.Flow
import java.util.UUID

/** Shown under the VoterId field when adding a voter whose VoterId is already taken. `%s` is the VoterId. */
const val DUPLICATE_VOTER_ID_MESSAGE =
    "A voter with VoterId %s already exists. Each VoterId must be unique; search for that voter or use a different VoterId."

sealed interface SaveResult {
    data class Saved(val id: String) : SaveResult
    data class Invalid(val errors: Map<String, String>) : SaveResult
}

/**
 * Search and single-record create/edit only. There is deliberately no bulk import, export or
 * delete here: regular users cannot do those in the app.
 */
class VoterRepository(
    private val db: AppDatabase,
    private val onChanged: () -> Unit = {},
    private val now: () -> String = { Timestamps.now() },
    private val newId: () -> String = { UUID.randomUUID().toString() },
) {
    private val dao = db.voterDao()

    fun search(query: String): Flow<List<VoterEntity>> = dao.search(likePattern(query), SEARCH_LIMIT)

    suspend fun get(id: String): VoterEntity? = dao.get(id)?.takeIf { it.deletedAt == null }

    /** Validates, stores the row as dirty, and asks for a sync. Returns the field errors if invalid. */
    suspend fun save(id: String?, form: VoterForm): SaveResult {
        val existing = id?.let { dao.get(it) }
        // VoterId is the unique key and is read-only once the voter exists: whatever the form
        // carries, an existing voter keeps its stored VoterId, so sync can never push a changed one.
        val locked = existing?.takeIf { it.deletedAt == null }
        val submitted = if (locked != null) form.copy(voterId = locked.voterId) else form
        val valid = when (val r = VoterValidator.validate(submitted)) {
            is VoterValidator.Result.Invalid -> return SaveResult.Invalid(r.errors)
            is VoterValidator.Result.Ok -> r.form
        }
        val recordId = existing?.id ?: id ?: newId()
        if (locked == null && dao.activeWithVoterId(valid.voterId, recordId) != null) {
            return SaveResult.Invalid(
                mapOf(FieldKey.VOTER_ID to DUPLICATE_VOTER_ID_MESSAGE.format(valid.voterId)),
            )
        }
        val stamp = now()
        val entity = VoterEntity(
            id = recordId,
            booth = valid.booth.ifEmpty { null },
            bsNo2024 = valid.bsNo2024.ifEmpty { null },
            ward = valid.ward.ifEmpty { null },
            wardSNo = valid.wardSNo.ifEmpty { null },
            voterId = valid.voterId,
            firstName = valid.firstName,
            surname = valid.surname.ifEmpty { null },
            relativeName = valid.relativeName.ifEmpty { null },
            houseNo = valid.houseNo.ifEmpty { null },
            gender = valid.gender,
            age = valid.age.toIntOrNull(),
            caste = valid.caste.ifEmpty { null },
            mobile = valid.mobile.ifEmpty { null },
            cCity = valid.cCity.ifEmpty { null },
            profession = valid.profession.ifEmpty { null },
            party = valid.party.ifEmpty { null },
            createdAt = existing?.createdAt ?: stamp,
            updatedAt = stamp,
            revision = existing?.revision ?: 0,
            deletedAt = null,
            sourceId = existing?.sourceId,
            dirty = true,
        )
        dao.upsert(entity)
        onChanged()
        return SaveResult.Saved(entity.id)
    }

    private fun likePattern(query: String): String {
        val text = query.trim()
        if (text.isEmpty()) return "%"
        val escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "%$escaped%"
    }

    private companion object {
        const val SEARCH_LIMIT = 200
    }
}

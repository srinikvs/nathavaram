package app.nathavaram.voters.store

import app.nathavaram.voters.domain.Timestamps
import app.nathavaram.voters.domain.VoterForm
import app.nathavaram.voters.domain.VoterValidator
import kotlinx.coroutines.flow.Flow
import java.util.UUID

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
        val valid = when (val r = VoterValidator.validate(form)) {
            is VoterValidator.Result.Invalid -> return SaveResult.Invalid(r.errors)
            is VoterValidator.Result.Ok -> r.form
        }
        val stamp = now()
        val existing = id?.let { dao.get(it) }
        val entity = VoterEntity(
            id = existing?.id ?: id ?: newId(),
            booth2024 = valid.booth2024.ifEmpty { null },
            bsNo2024 = valid.bsNo2024.ifEmpty { null },
            ward2026 = valid.ward2026.ifEmpty { null },
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

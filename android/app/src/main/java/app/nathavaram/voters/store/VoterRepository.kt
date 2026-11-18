package app.nathavaram.voters.store

import app.nathavaram.voters.domain.FieldKey
import app.nathavaram.voters.domain.Timestamps
import app.nathavaram.voters.domain.VoterForm
import app.nathavaram.voters.domain.Vocab
import app.nathavaram.voters.domain.VoterValidator
import kotlinx.coroutines.flow.Flow

/** Shown under the VoterId field when adding a voter whose VoterId is already taken. `%s` is the VoterId. */
const val DUPLICATE_VOTER_ID_MESSAGE =
    "A voter with VoterId %s already exists. Each VoterId must be unique; search for that voter or use a different VoterId."

sealed interface SaveResult {
    data class Saved(val voterId: String) : SaveResult
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
    /**
     * True for an owner/admin, who may edit every field except VoterId. False restricts an existing
     * voter's edit to Party, Caste, Mobile, HouseNo and Street. The app wires this to the signed-in role.
     */
    private val canEditAllFields: () -> Boolean = { true },
) {
    private val dao = db.voterDao()

    fun search(query: String): Flow<List<VoterEntity>> = dao.search(likePattern(query), SEARCH_LIMIT)

    suspend fun get(voterId: String): VoterEntity? = dao.get(voterId)?.takeIf { it.deletedAt == null }

    /**
     * Validates, stores the row as dirty, and asks for a sync. Returns the field errors if invalid.
     *
     * [editingVoterId] is the voter being edited, or null when adding. VoterId is the primary key:
     * typed once when adding, never changed afterwards. Adding a VoterId that is already active is
     * refused; adding one that only exists as a soft-deleted row revives that row, as on the server.
     */
    suspend fun save(editingVoterId: String?, form: VoterForm): SaveResult {
        val existing = editingVoterId?.let { dao.get(it) }
        // Whatever the form carries, an existing voter keeps its stored VoterId, so sync can
        // never push a changed one.
        val locked = existing?.takeIf { it.deletedAt == null }
        var submitted = if (locked != null) form.copy(voterId = locked.voterId) else form
        // A regular user may change only Party, Caste, Mobile, HouseNo and Street of an existing
        // voter. Everything else is taken from the stored row, so a restricted change is never saved
        // and never reaches the sync payload. The server enforces the same rule.
        if (locked != null && !canEditAllFields()) {
            submitted = submitted.restrictedTo(VoterForm.from(locked))
        }
        val valid = when (val r = VoterValidator.validate(submitted)) {
            is VoterValidator.Result.Invalid -> return SaveResult.Invalid(r.errors)
            is VoterValidator.Result.Ok -> r.form
        }
        val current = locked ?: dao.get(valid.voterId)
        if (locked == null && current != null && current.deletedAt == null) {
            return SaveResult.Invalid(
                mapOf(FieldKey.VOTER_ID to DUPLICATE_VOTER_ID_MESSAGE.format(valid.voterId)),
            )
        }
        val stamp = now()
        val entity = VoterEntity(
            voterId = valid.voterId,
            booth = valid.booth.ifEmpty { null },
            boothSNo = valid.boothSNo.ifEmpty { null },
            ward = valid.ward.ifEmpty { null },
            wardSNo = valid.wardSNo.ifEmpty { null },
            firstName = valid.firstName,
            lastName = valid.lastName.ifEmpty { null },
            relativeName = valid.relativeName.ifEmpty { null },
            houseNo = valid.houseNo.ifEmpty { null },
            age = valid.age.toIntOrNull(),
            street = valid.street.ifEmpty { null },
            mobile = valid.mobile.ifEmpty { null },
            caste = valid.caste.ifEmpty { null },
            village = valid.village.ifEmpty { null },
            panchayath = valid.panchayath.ifEmpty { null },
            mandal = valid.mandal.ifEmpty { null },
            currentCity = valid.currentCity.ifEmpty { null },
            isLocal = valid.isLocal == Vocab.LOCAL,
            party = valid.party.ifEmpty { null },
            // A revived row is a new voter: it gets a new created_at but keeps the tombstone's
            // revision, so the push carries a current base_revision.
            createdAt = locked?.createdAt ?: stamp,
            updatedAt = stamp,
            revision = current?.revision ?: 0,
            deletedAt = null,
            sourceId = current?.sourceId,
            dirty = true,
        )
        dao.upsert(entity)
        onChanged()
        return SaveResult.Saved(entity.voterId)
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

package app.nathavaram.voters.sync

import app.nathavaram.voters.store.ConflictEntity
import app.nathavaram.voters.store.VoterEntity
import app.nathavaram.voters.domain.Timestamps

enum class PushKind { APPLIED, CONFLICT, FORBIDDEN, VOTER_ID_CONFLICT, VALIDATION_ERROR, UNKNOWN }

/** What to write after one push result. Pure data, so the rules can be unit tested. */
data class PushDecision(
    val kind: PushKind,
    val store: List<VoterEntity> = emptyList(),
    val deleteIds: List<String> = emptyList(),
    val conflict: ConflictEntity? = null,
    /** The local row could not be refreshed from a result, so pull everything again. */
    val rewindCursor: Boolean = false,
)

/** What to write for one pulled change. */
data class PullDecision(val entity: VoterEntity, val keptLocal: Boolean)

/** The conflict/merge rules from docs/SYNC.md, free of I/O. */
object SyncMerge {

    fun toPushChange(e: VoterEntity): PushChange = PushChange(
        id = e.id,
        updatedAt = e.updatedAt,
        createdAt = e.createdAt,
        deleted = false,
        baseRevision = e.revision.takeIf { it > 0 },
        booth = e.booth, bsNo2024 = e.bsNo2024, ward = e.ward, wardSNo = e.wardSNo,
        voterId = e.voterId, firstName = e.firstName, surname = e.surname,
        relativeName = e.relativeName, address = e.address, village = e.village,
        permanentAddress = e.permanentAddress, isLocal = e.isLocal, gender = e.gender, age = e.age,
        caste = e.caste, mobile = e.mobile, cCity = e.cCity, profession = e.profession,
        party = e.party,
    )

    fun toDto(e: VoterEntity): VoterDto = VoterDto(
        id = e.id,
        booth = e.booth, bsNo2024 = e.bsNo2024, ward = e.ward, wardSNo = e.wardSNo,
        voterId = e.voterId, firstName = e.firstName, surname = e.surname,
        relativeName = e.relativeName, address = e.address, village = e.village,
        permanentAddress = e.permanentAddress, isLocal = e.isLocal, gender = e.gender, age = e.age,
        caste = e.caste, mobile = e.mobile, cCity = e.cCity, profession = e.profession,
        party = e.party, createdAt = e.createdAt, updatedAt = e.updatedAt, revision = e.revision,
        deletedAt = e.deletedAt, sourceId = e.sourceId,
    )

    /** A clean local row from a server record. A deleted record becomes a stripped tombstone. */
    fun fromDto(dto: VoterDto): VoterEntity =
        if (dto.deletedAt != null) tombstone(dto) else VoterEntity(
            id = dto.id,
            booth = dto.booth, bsNo2024 = dto.bsNo2024, ward = dto.ward,
            wardSNo = dto.wardSNo, voterId = dto.voterId, firstName = dto.firstName,
            surname = dto.surname, relativeName = dto.relativeName, address = dto.address,
            village = dto.village, permanentAddress = dto.permanentAddress, isLocal = dto.isLocal,
            gender = dto.gender, age = dto.age, caste = dto.caste, mobile = dto.mobile,
            cCity = dto.cCity, profession = dto.profession, party = dto.party,
            createdAt = dto.createdAt, updatedAt = dto.updatedAt, revision = dto.revision,
            deletedAt = null, sourceId = dto.sourceId, dirty = false,
        )

    /**
     * Keeps only id, timestamps, revision and source. Nothing the server sent in the personal
     * columns is trusted: `voter_id` of a tombstone is an inert placeholder, never an EPIC.
     */
    fun tombstone(dto: VoterDto): VoterEntity = VoterEntity(
        id = dto.id,
        voterId = dto.id.replace("-", ""),
        firstName = "",
        gender = "Unknown",
        createdAt = dto.createdAt,
        updatedAt = dto.updatedAt,
        revision = dto.revision,
        deletedAt = dto.deletedAt,
        sourceId = dto.sourceId,
        dirty = false,
    )

    /**
     * A still-dirty local row that is newer than the pulled one is not overwritten, because it
     * still has to be pushed. Its revision moves up to the one just seen, so the push carries a
     * current `base_revision` and wins by `updated_at` instead of being rejected as stale.
     */
    fun applyPulled(local: VoterEntity?, dto: VoterDto): PullDecision {
        if (local != null && local.dirty && Timestamps.isNewer(local.updatedAt, dto.updatedAt)) {
            return PullDecision(local.copy(revision = maxOf(local.revision, dto.revision)), keptLocal = true)
        }
        return PullDecision(fromDto(dto), keptLocal = false)
    }

    /**
     * [local] is the row as stored now (null if it vanished), [pushed] the snapshot that was sent.
     * Server wins on `conflict` and `voter_id_conflict`; the operator gets a [ConflictEntity].
     */
    fun applyPushResult(
        local: VoterEntity?,
        pushed: VoterEntity,
        result: PushResult,
        now: String,
    ): PushDecision {
        val record = result.serverRecord
        return when (result.status) {
            ResultStatus.APPLIED -> applied(local, pushed, result)
            ResultStatus.CONFLICT -> serverWins(PushKind.CONFLICT, ResultStatus.CONFLICT, local, pushed, result, now)
            // The server refused a field the user role may not change. It applied nothing, so the
            // server row replaces the local edit and the operator sees the reason.
            ResultStatus.FORBIDDEN -> serverWins(PushKind.FORBIDDEN, ResultStatus.FORBIDDEN, local, pushed, result, now)
            ResultStatus.VOTER_ID_CONFLICT -> voterIdConflict(local, pushed, result, now)
            ResultStatus.VALIDATION_ERROR -> PushDecision(
                PushKind.VALIDATION_ERROR,
                conflict = conflictNote(pushed.id, ResultStatus.VALIDATION_ERROR, result, local ?: pushed, null, now),
            )
            else -> PushDecision(PushKind.UNKNOWN)
        }
    }

    private fun serverWins(
        kind: PushKind,
        status: String,
        local: VoterEntity?,
        pushed: VoterEntity,
        result: PushResult,
        now: String,
    ): PushDecision {
        val record = result.serverRecord
        val note = conflictNote(pushed.id, status, result, local ?: pushed, record, now)
        return if (record == null) PushDecision(kind, conflict = note)
        else PushDecision(kind, store = listOf(fromDto(record)), conflict = note)
    }

    private fun applied(local: VoterEntity?, pushed: VoterEntity, result: PushResult): PushDecision {
        val record = result.serverRecord
        if (local == null) {
            return PushDecision(PushKind.APPLIED, store = listOfNotNull(record?.let(::fromDto)))
        }
        val seen = record?.revision ?: result.serverRevision ?: local.revision
        // Edited again while the push was in flight: keep the newer edit and push it next time.
        if (local.updatedAt != pushed.updatedAt) {
            return PushDecision(
                PushKind.APPLIED,
                store = listOf(local.copy(revision = maxOf(local.revision, seen), dirty = true)),
            )
        }
        val clean = record?.let(::fromDto) ?: local.copy(revision = seen, dirty = false)
        return PushDecision(PushKind.APPLIED, store = listOf(clean))
    }

    private fun voterIdConflict(
        local: VoterEntity?,
        pushed: VoterEntity,
        result: PushResult,
        now: String,
    ): PushDecision {
        val record = result.serverRecord
        val note = conflictNote(pushed.id, ResultStatus.VOTER_ID_CONFLICT, result, local ?: pushed, record, now)
        if (record == null) return PushDecision(PushKind.VOTER_ID_CONFLICT, conflict = note)
        if (record.id.equals(pushed.id, ignoreCase = true)) {
            return PushDecision(PushKind.VOTER_ID_CONFLICT, store = listOf(fromDto(record)), conflict = note)
        }
        // The record is a different person who already owns this VoterId.
        val other = fromDto(record)
        return when {
            // The server never saw this row, so dropping it locally loses nothing else.
            local == null || local.revision == 0L ->
                PushDecision(PushKind.VOTER_ID_CONFLICT, store = listOf(other), deleteIds = listOf(pushed.id), conflict = note)
            // The server has an older copy of this row that this device cannot see; pull it again.
            else -> PushDecision(
                PushKind.VOTER_ID_CONFLICT,
                store = listOf(other, local.copy(dirty = false)),
                conflict = note,
                rewindCursor = true,
            )
        }
    }

    private fun conflictNote(
        id: String,
        kind: String,
        result: PushResult,
        localSnapshot: VoterEntity,
        server: VoterDto?,
        now: String,
    ): ConflictEntity = ConflictEntity(
        voterUuid = id,
        kind = kind,
        message = result.message,
        detectedAt = now,
        localJson = SyncJson.json.encodeToString(VoterDto.serializer(), toDto(localSnapshot)),
        serverJson = server?.let { SyncJson.json.encodeToString(VoterDto.serializer(), it) },
    )
}

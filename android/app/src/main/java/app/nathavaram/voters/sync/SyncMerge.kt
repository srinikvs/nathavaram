package app.nathavaram.voters.sync

import app.nathavaram.voters.store.ConflictEntity
import app.nathavaram.voters.store.VoterEntity
import app.nathavaram.voters.domain.Timestamps

enum class PushKind { APPLIED, CONFLICT, FORBIDDEN, VALIDATION_ERROR, UNKNOWN }

/** What to write after one push result. Pure data, so the rules can be unit tested. */
data class PushDecision(
    val kind: PushKind,
    val store: List<VoterEntity> = emptyList(),
    val conflict: ConflictEntity? = null,
)

/** What to write for one pulled change. */
data class PullDecision(val entity: VoterEntity, val keptLocal: Boolean)

/** The conflict/merge rules from docs/SYNC.md, free of I/O. */
object SyncMerge {

    fun toPushChange(e: VoterEntity): PushChange = PushChange(
        voterId = e.voterId,
        updatedAt = e.updatedAt,
        createdAt = e.createdAt,
        deleted = false,
        baseRevision = e.revision.takeIf { it > 0 },
        booth = e.booth, boothSNo = e.boothSNo, ward = e.ward, wardSNo = e.wardSNo,
        firstName = e.firstName, lastName = e.lastName, relativeName = e.relativeName,
        houseNo = e.houseNo, age = e.age, street = e.street, mobile = e.mobile, caste = e.caste,
        village = e.village, panchayath = e.panchayath, mandal = e.mandal,
        currentCity = e.currentCity, isLocal = e.isLocal, party = e.party,
        constituency = e.constituency,
    )

    fun toDto(e: VoterEntity): VoterDto = VoterDto(
        voterId = e.voterId,
        booth = e.booth, boothSNo = e.boothSNo, ward = e.ward, wardSNo = e.wardSNo,
        firstName = e.firstName, lastName = e.lastName, relativeName = e.relativeName,
        houseNo = e.houseNo, age = e.age, street = e.street, mobile = e.mobile, caste = e.caste,
        village = e.village, panchayath = e.panchayath, mandal = e.mandal,
        currentCity = e.currentCity, isLocal = e.isLocal, party = e.party,
        constituency = e.constituency,
        createdAt = e.createdAt, updatedAt = e.updatedAt, revision = e.revision,
        deletedAt = e.deletedAt, sourceId = e.sourceId,
    )

    /** A clean local row from a server record. A deleted record becomes a stripped tombstone. */
    fun fromDto(dto: VoterDto): VoterEntity =
        if (dto.deletedAt != null) tombstone(dto) else VoterEntity(
            voterId = dto.voterId,
            booth = dto.booth, boothSNo = dto.boothSNo, ward = dto.ward, wardSNo = dto.wardSNo,
            firstName = dto.firstName, lastName = dto.lastName, relativeName = dto.relativeName,
            houseNo = dto.houseNo, age = dto.age, street = dto.street, mobile = dto.mobile,
            caste = dto.caste, village = dto.village, panchayath = dto.panchayath,
            mandal = dto.mandal, currentCity = dto.currentCity, isLocal = dto.isLocal,
            party = dto.party, constituency = dto.constituency, createdAt = dto.createdAt, updatedAt = dto.updatedAt,
            revision = dto.revision, deletedAt = null, sourceId = dto.sourceId, dirty = false,
        )

    /**
     * Keeps only the key, timestamps, revision and source. Nothing the server sent in the
     * personal columns is trusted, so a tombstone never carries personal data locally.
     */
    fun tombstone(dto: VoterDto): VoterEntity = VoterEntity(
        voterId = dto.voterId,
        firstName = "",
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
     * Server wins on `conflict` and `forbidden`; the operator gets a [ConflictEntity].
     */
    fun applyPushResult(
        local: VoterEntity?,
        pushed: VoterEntity,
        result: PushResult,
        now: String,
    ): PushDecision {
        return when (result.status) {
            ResultStatus.APPLIED -> applied(local, pushed, result)
            ResultStatus.CONFLICT -> serverWins(PushKind.CONFLICT, ResultStatus.CONFLICT, local, pushed, result, now)
            // The server refused a field the user role may not change. It applied nothing, so the
            // server row replaces the local edit and the operator sees the reason.
            ResultStatus.FORBIDDEN -> serverWins(PushKind.FORBIDDEN, ResultStatus.FORBIDDEN, local, pushed, result, now)
            ResultStatus.VALIDATION_ERROR -> PushDecision(
                PushKind.VALIDATION_ERROR,
                conflict = conflictNote(pushed.voterId, ResultStatus.VALIDATION_ERROR, result, local ?: pushed, null, now),
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
        val note = conflictNote(pushed.voterId, status, result, local ?: pushed, record, now)
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

    private fun conflictNote(
        voterId: String,
        kind: String,
        result: PushResult,
        localSnapshot: VoterEntity,
        server: VoterDto?,
        now: String,
    ): ConflictEntity = ConflictEntity(
        voterId = voterId,
        kind = kind,
        message = result.message,
        detectedAt = now,
        localJson = SyncJson.json.encodeToString(VoterDto.serializer(), toDto(localSnapshot)),
        serverJson = server?.let { SyncJson.json.encodeToString(VoterDto.serializer(), it) },
    )
}

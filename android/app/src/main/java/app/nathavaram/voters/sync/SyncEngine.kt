package app.nathavaram.voters.sync

import androidx.room.withTransaction
import app.nathavaram.voters.store.AppDatabase
import app.nathavaram.voters.store.SyncStateEntity
import app.nathavaram.voters.domain.ClockSkew
import app.nathavaram.voters.domain.Timestamps
import java.time.Instant

/** The HTTP calls the engine needs. Faked in tests. */
interface SyncApi {
    suspend fun pull(since: Long, limit: Int): PullResponse
    suspend fun push(request: PushRequest): PushResponse
}

data class SyncSummary(
    val applied: Int = 0,
    val conflicts: Int = 0,
    val validationErrors: Int = 0,
    val pulled: Int = 0,
    val clockSkew: Boolean = false,
) {
    val needsAttention: Boolean get() = conflicts + validationErrors > 0
}

/** Push dirty rows, then pull until `has_more` is false (docs/SYNC.md, "What the Android client should do"). */
class SyncEngine(
    private val db: AppDatabase,
    private val api: SyncApi,
    private val deviceId: String,
    private val now: () -> String = { Timestamps.now() },
    private val nowInstant: () -> Instant = { Instant.now() },
) {
    private val dao = db.voterDao()
    private val conflictDao = db.conflictDao()

    private class Tally {
        var applied = 0
        var conflicts = 0
        var validationErrors = 0
        var pulled = 0
        var clockSkew = false
    }

    suspend fun run(): SyncSummary {
        val tally = Tally()
        push(tally)
        pull(tally)
        return SyncSummary(
            tally.applied, tally.conflicts,
            tally.validationErrors, tally.pulled, tally.clockSkew,
        )
    }

    private suspend fun push(tally: Tally) {
        for (batch in dao.dirtyRows().chunked(PUSH_BATCH_LIMIT)) {
            val response = api.push(PushRequest(deviceId, batch.map(SyncMerge::toPushChange)))
            noteServerTime(response.serverTime, tally)
            val sent = batch.associateBy { it.voterId.uppercase() }
            db.withTransaction {
                // Each result is applied on its own: one conflict never drops the others.
                for (result in response.results) {
                    val pushed = sent[result.voterId.uppercase()] ?: continue
                    val decision = SyncMerge.applyPushResult(dao.get(pushed.voterId), pushed, result, now())
                    persist(pushed.voterId, decision, tally)
                }
            }
        }
    }

    private suspend fun persist(voterId: String, decision: PushDecision, tally: Tally) {
        if (decision.store.isNotEmpty()) dao.upsertAll(decision.store)
        decision.conflict?.let { conflictDao.upsert(it) }
        if (decision.kind == PushKind.APPLIED) conflictDao.delete(voterId)
        when (decision.kind) {
            PushKind.APPLIED -> tally.applied++
            PushKind.CONFLICT, PushKind.FORBIDDEN -> tally.conflicts++
            PushKind.VALIDATION_ERROR -> tally.validationErrors++
            PushKind.UNKNOWN -> Unit
        }
    }

    private suspend fun pull(tally: Tally) {
        var cursor = dao.cursor() ?: 0L
        while (true) {
            val page = api.pull(cursor, PULL_LIMIT)
            noteServerTime(page.serverTime, tally)
            val next = db.withTransaction {
                for (dto in page.changes) {
                    dao.upsert(SyncMerge.applyPulled(dao.get(dto.voterId), dto).entity)
                }
                // The cursor moves only after the whole page is stored.
                val stored = page.changes.lastOrNull()?.revision ?: maxOf(cursor, page.serverRevision)
                dao.putState(SyncStateEntity(serverRevision = stored))
                stored
            }
            tally.pulled += page.changes.size
            if (!page.hasMore || next <= cursor) break
            cursor = next
        }
    }

    private fun noteServerTime(serverTime: String, tally: Tally) {
        if (ClockSkew.exceeds(serverTime, nowInstant())) tally.clockSkew = true
    }

    companion object {
        const val PUSH_BATCH_LIMIT = 500
        const val PULL_LIMIT = 500
    }
}

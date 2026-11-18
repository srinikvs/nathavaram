package app.nathavaram.voters.sync

import app.nathavaram.voters.Fixtures
import app.nathavaram.voters.Fixtures.VOTER_1
import app.nathavaram.voters.Fixtures.VOTER_2
import app.nathavaram.voters.Fixtures.T1
import app.nathavaram.voters.Fixtures.T2
import app.nathavaram.voters.Fixtures.T3
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncMergeTest {

    private fun result(status: String, record: VoterDto? = null, revision: Long? = null, message: String? = null) =
        PushResult(voterId = VOTER_1, status = status, message = message, serverRevision = revision, serverRecord = record)

    // --- push snapshot ---

    @Test
    fun `push change is a full snapshot keyed on voter_id with no base revision for a new row`() {
        val change = SyncMerge.toPushChange(Fixtures.entity(revision = 0, dirty = true))

        assertEquals(VOTER_1, change.voterId)
        assertEquals("12-A", change.houseNo)
        assertEquals("Main Street", change.street)
        assertNull(change.baseRevision)
        assertFalse(change.deleted)
    }

    @Test
    fun `push change carries the last seen server revision as base_revision`() {
        val change = SyncMerge.toPushChange(Fixtures.entity(revision = 9, dirty = true))

        assertEquals(9L, change.baseRevision)
    }

    // --- applied ---

    @Test
    fun `applied replaces the local row with server_record and clears dirty`() {
        val pushed = Fixtures.entity(updatedAt = T2, revision = 3, dirty = true)
        val record = Fixtures.dto(updatedAt = T2, revision = 8, party = "BJP")

        val decision = SyncMerge.applyPushResult(pushed, pushed, result("applied", record), T3)

        val stored = decision.store.single()
        assertEquals(PushKind.APPLIED, decision.kind)
        assertFalse(stored.dirty)
        assertEquals(8L, stored.revision)
        assertEquals("BJP", stored.party)
        assertNull(decision.conflict)
    }

    @Test
    fun `applied keeps a newer local edit dirty and moves its base revision forward`() {
        val pushed = Fixtures.entity(updatedAt = T1, revision = 3, dirty = true)
        val editedMeanwhile = pushed.copy(updatedAt = T3, firstName = "Edited")
        val record = Fixtures.dto(updatedAt = T1, revision = 8)

        val decision = SyncMerge.applyPushResult(editedMeanwhile, pushed, result("applied", record), T3)

        val stored = decision.store.single()
        assertTrue(stored.dirty)
        assertEquals("Edited", stored.firstName)
        assertEquals(8L, stored.revision)
    }

    // --- conflict ---

    @Test
    fun `stale base revision conflict replaces local row with the server record and reports it`() {
        val pushed = Fixtures.entity(updatedAt = T3, revision = 2, dirty = true, firstName = "LocalEdit")
        val record = Fixtures.dto(updatedAt = T2, revision = 6, firstName = "ServerEdit")

        val decision = SyncMerge.applyPushResult(pushed, pushed, result("conflict", record, message = "stale"), T3)

        val stored = decision.store.single()
        assertEquals(PushKind.CONFLICT, decision.kind)
        assertEquals("ServerEdit", stored.firstName)
        assertEquals(6L, stored.revision)
        assertFalse(stored.dirty)
        val conflict = decision.conflict
        assertNotNull(conflict)
        assertEquals("conflict", conflict!!.kind)
        assertEquals("LocalEdit", ConflictDiff.decode(conflict.localJson)?.firstName)
        assertEquals("ServerEdit", ConflictDiff.decode(conflict.serverJson)?.firstName)
    }

    @Test
    fun `conflict without a server record leaves the local row dirty`() {
        val pushed = Fixtures.entity(dirty = true)

        val decision = SyncMerge.applyPushResult(pushed, pushed, result("conflict"), T3)

        assertTrue(decision.store.isEmpty())
        assertNotNull(decision.conflict)
    }

    @Test
    fun `conflict diff lists only the fields that differ`() {
        val local = SyncMerge.toDto(Fixtures.entity(firstName = "LocalEdit", party = "TDP"))
        val server = Fixtures.dto(firstName = "ServerEdit", party = "TDP")

        val labels = ConflictDiff.diff(local, server).map { it.label }

        assertEquals(listOf("FirstName"), labels)
    }

    // --- validation_error ---

    @Test
    fun `validation_error keeps the row dirty and records the message`() {
        val pushed = Fixtures.entity(dirty = true)

        val decision = SyncMerge.applyPushResult(pushed, pushed, result("validation_error", message = "bad age"), T3)

        assertEquals(PushKind.VALIDATION_ERROR, decision.kind)
        assertTrue(decision.store.isEmpty())
        assertEquals("bad age", decision.conflict?.message)
    }

    // --- tombstones ---

    @Test
    fun `tombstone from pull keeps only the key and drops any personal data the server sent`() {
        val hostile = Fixtures.tombstoneDto().copy(firstName = "Leaked", mobile = "9000000009", lastName = "Leak", street = "Leak St")

        val decision = SyncMerge.applyPulled(Fixtures.entity(), hostile)

        val stored = decision.entity
        assertNotNull(stored.deletedAt)
        assertEquals(VOTER_1, stored.voterId)
        assertEquals("", stored.firstName)
        assertNull(stored.mobile)
        assertNull(stored.lastName)
        assertNull(stored.street)
        assertNull(stored.age)
        assertFalse(stored.dirty)
        assertEquals(7L, stored.revision)
    }

    @Test
    fun `a newer record for a tombstoned voter id revives it`() {
        val tombstone = SyncMerge.fromDto(Fixtures.tombstoneDto(revision = 7))
        val revived = Fixtures.dto(updatedAt = "2026-11-11T13:00:00.000000Z", revision = 8, firstName = "Revived")

        val decision = SyncMerge.applyPulled(tombstone, revived)

        assertEquals("Revived", decision.entity.firstName)
        assertNull(decision.entity.deletedAt)
        assertEquals(8L, decision.entity.revision)
    }

    @Test
    fun `tombstone for an unknown voter id is stored so the row is not resurrected later`() {
        val decision = SyncMerge.applyPulled(null, Fixtures.tombstoneDto())

        assertNotNull(decision.entity.deletedAt)
        assertFalse(decision.keptLocal)
    }

    // --- pull vs dirty ---

    @Test
    fun `pull does not overwrite a dirty local row that is newer`() {
        val local = Fixtures.entity(updatedAt = T3, revision = 2, dirty = true, firstName = "LocalNewer")
        val pulled = Fixtures.dto(updatedAt = T2, revision = 9, firstName = "ServerOlder")

        val decision = SyncMerge.applyPulled(local, pulled)

        assertTrue(decision.keptLocal)
        assertEquals("LocalNewer", decision.entity.firstName)
        assertTrue(decision.entity.dirty)
        assertEquals("revision moves up so the next push is not rejected as stale", 9L, decision.entity.revision)
    }

    @Test
    fun `pull overwrites a dirty local row that is older`() {
        val local = Fixtures.entity(updatedAt = T1, revision = 2, dirty = true, firstName = "LocalOlder")
        val pulled = Fixtures.dto(updatedAt = T2, revision = 9, firstName = "ServerNewer")

        val decision = SyncMerge.applyPulled(local, pulled)

        assertFalse(decision.keptLocal)
        assertEquals("ServerNewer", decision.entity.firstName)
        assertFalse(decision.entity.dirty)
    }

    @Test
    fun `pull overwrites a clean local row`() {
        val local = Fixtures.entity(updatedAt = T3, revision = 2, dirty = false)
        val pulled = Fixtures.dto(updatedAt = T2, revision = 9, firstName = "ServerCopy")

        val decision = SyncMerge.applyPulled(local, pulled)

        assertEquals("ServerCopy", decision.entity.firstName)
    }
}

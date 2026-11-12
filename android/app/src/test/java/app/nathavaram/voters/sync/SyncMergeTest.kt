package app.nathavaram.voters.sync

import app.nathavaram.voters.Fixtures
import app.nathavaram.voters.Fixtures.ID_1
import app.nathavaram.voters.Fixtures.ID_2
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
        PushResult(id = ID_1, status = status, message = message, serverRevision = revision, serverRecord = record)

    // --- push snapshot ---

    @Test
    fun `push change is a full snapshot with explicit gender and no base revision for a new row`() {
        val change = SyncMerge.toPushChange(Fixtures.entity(revision = 0, dirty = true))

        assertEquals(ID_1, change.id)
        assertEquals("Male", change.gender)
        assertNull(change.baseRevision)
        assertFalse(change.deleted)
        assertEquals("ZZZ0000001", change.voterId)
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

    // --- voter_id_conflict ---

    @Test
    fun `voter_id_conflict on a never-synced row drops it and keeps the other voter`() {
        val pushed = Fixtures.entity(id = ID_1, revision = 0, dirty = true)
        val other = Fixtures.dto(id = ID_2, voterId = "ZZZ0000001", firstName = "Otherperson", revision = 4)

        val decision = SyncMerge.applyPushResult(pushed, pushed, result("voter_id_conflict", other), T3)

        assertEquals(PushKind.VOTER_ID_CONFLICT, decision.kind)
        assertEquals(listOf(ID_1), decision.deleteIds)
        assertEquals(ID_2, decision.store.single().id)
        assertFalse(decision.store.single().dirty)
        assertFalse(decision.rewindCursor)
        assertEquals("voter_id_conflict", decision.conflict?.kind)
    }

    @Test
    fun `voter_id_conflict on a previously synced row keeps it clean and asks for a full pull`() {
        val pushed = Fixtures.entity(id = ID_1, revision = 3, dirty = true)
        val other = Fixtures.dto(id = ID_2, revision = 4)

        val decision = SyncMerge.applyPushResult(pushed, pushed, result("voter_id_conflict", other), T3)

        assertTrue(decision.deleteIds.isEmpty())
        assertTrue(decision.rewindCursor)
        assertTrue(decision.store.none { it.dirty })
        assertEquals(setOf(ID_1, ID_2), decision.store.map { it.id }.toSet())
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
    fun `tombstone from pull is stored stripped and never read as a real EPIC`() {
        val hostile = Fixtures.tombstoneDto().copy(voterId = "ZZZ0000009", firstName = "Leaked", mobile = "9000000009")

        val decision = SyncMerge.applyPulled(Fixtures.entity(), hostile)

        val stored = decision.entity
        assertNotNull(stored.deletedAt)
        assertEquals(ID_1.replace("-", ""), stored.voterId)
        assertEquals("", stored.firstName)
        assertEquals("Unknown", stored.gender)
        assertNull(stored.mobile)
        assertNull(stored.surname)
        assertNull(stored.age)
        assertFalse(stored.dirty)
        assertEquals(7L, stored.revision)
    }

    @Test
    fun `tombstone for an unknown id is stored so the row is not resurrected later`() {
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

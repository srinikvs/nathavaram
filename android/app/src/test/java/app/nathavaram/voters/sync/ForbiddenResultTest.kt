package app.nathavaram.voters.sync

import app.nathavaram.voters.Fixtures
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The server's "forbidden" push result (user role, restricted field) and the new sync fields. Synthetic data only. */
class ForbiddenResultTest {
    private val now = "2026-11-11T13:00:00.000000Z"

    private fun forbidden(record: VoterDto?) = PushResult(
        id = Fixtures.ID_1,
        status = ResultStatus.FORBIDDEN,
        message = "Your role cannot change: Ward.",
        serverRevision = record?.revision,
        serverRecord = record,
    )

    @Test
    fun `a forbidden result puts the server row back and tells the operator why`() {
        val local = Fixtures.entity(dirty = true, revision = 5).copy(ward = "9")
        val server = Fixtures.dto(revision = 5).copy(ward = "3")

        val decision = SyncMerge.applyPushResult(local, local, forbidden(server), now)

        assertEquals(PushKind.FORBIDDEN, decision.kind)
        val stored = decision.store.single()
        assertEquals("3", stored.ward)
        assertFalse(stored.dirty)
        val note = decision.conflict!!
        assertEquals(ResultStatus.FORBIDDEN, note.kind)
        assertTrue(note.message!!.contains("cannot change"))
    }

    @Test
    fun `a forbidden result without a server record only records the note`() {
        val local = Fixtures.entity(dirty = true)

        val decision = SyncMerge.applyPushResult(local, local, forbidden(null), now)

        assertEquals(PushKind.FORBIDDEN, decision.kind)
        assertTrue(decision.store.isEmpty())
        assertNotNull(decision.conflict)
    }

    @Test
    fun `push change and pulled record carry address village permanent address and local, not house no`() {
        val entity = Fixtures.entity(village = "Samplevillage", isLocal = true).copy(permanentAddress = "Sample Lane 5")

        val push = SyncMerge.toPushChange(entity)
        val json = SyncJson.json.encodeToString(PushChange.serializer(), push)
        val back = SyncMerge.fromDto(SyncMerge.toDto(entity))

        assertEquals("H.No 12-A", push.address)
        assertEquals("Samplevillage", push.village)
        assertEquals("Sample Lane 5", push.permanentAddress)
        assertTrue(push.isLocal)
        assertTrue("\"is_local\":true" in json && "\"permanent_address\"" in json)
        assertFalse("house_no" in json)
        assertEquals(entity.copy(dirty = false), back.copy(dirty = false))
    }

    @Test
    fun `a pushed voter id is always the stored one`() {
        val entity = Fixtures.entity(voterId = "ZZZ0000001")

        assertEquals("ZZZ0000001", SyncMerge.toPushChange(entity).voterId)
    }

    @Test
    fun `conflict screen lists the new fields and no house no`() {
        val local = Fixtures.dto().copy(address = "A", village = "V1", permanentAddress = "P1", isLocal = true)
        val server = Fixtures.dto().copy(address = "B", village = "V2", permanentAddress = "P2", isLocal = false)

        val labels = ConflictDiff.diff(local, server).map { it.label }

        assertTrue(labels.containsAll(listOf("Address", "Village", "Permanent address", "Local")))
        assertFalse("HouseNo" in labels)
        assertNull(ConflictDiff.diff(local, local).firstOrNull())
    }
}

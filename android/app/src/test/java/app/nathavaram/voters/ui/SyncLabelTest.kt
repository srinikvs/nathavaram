package app.nathavaram.voters.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncLabelTest {
    @Test
    fun `no pending changes shows plain Sync`() {
        assertEquals("Sync", SyncLabel.forPending(0))
    }

    @Test
    fun `one pending change shows a short label with the count`() {
        val label = SyncLabel.forPending(1)

        assertTrue(label.contains("1"))
        assertTrue(label.length <= "Sync (99)".length)
    }
}

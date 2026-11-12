package app.nathavaram.voters.ui

/** Pure label helpers for the search toolbar, kept short so the bar fits a 412dp-wide phone. */
object SyncLabel {
    fun forPending(pending: Int): String = if (pending > 0) "Sync ($pending)" else "Sync"
}

package app.nathavaram.voters.security

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/** Clears the stored session when the server rejects the token (401) and tells the UI to show Login. */
class SessionGuard(private val tokens: TokenStore) {
    private val _expired = MutableSharedFlow<Unit>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val expired: SharedFlow<Unit> = _expired.asSharedFlow()

    fun onUnauthorized() {
        tokens.clear()
        _expired.tryEmit(Unit)
    }
}

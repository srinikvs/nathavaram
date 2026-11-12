package app.nathavaram.voters.security

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SessionGuardTest {
    private class FakeTokens(var jwt: String? = "fake-token-not-real") : TokenStore {
        override fun token() = jwt
        override fun username() = "fakeuser"
        override fun save(token: String, username: String) { jwt = token }
        override fun clear() { jwt = null }
    }

    @Test
    fun `unauthorized clears the token and emits an expiry event for the UI`() = runTest(UnconfinedTestDispatcher()) {
        val tokens = FakeTokens()
        val guard = SessionGuard(tokens)
        var events = 0
        val job = launch { guard.expired.collect { events++ } }

        guard.onUnauthorized()

        assertNull(tokens.token())
        assertEquals(1, events)
        job.cancel()
    }
}

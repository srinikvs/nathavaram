package app.nathavaram.voters.net

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class PasswordChangeRequiredParsingTest {
    private val nested =
        """{"detail":{"code":"password_change_required","message":"Password change required. POST /api/auth/change-password first."}}"""

    @Test
    fun `nested detail code on a 403 is a forced password change`() {
        assertTrue(PasswordChangeRequired.matches(403, nested))
    }

    @Test
    fun `top-level code on a 403 is a forced password change`() {
        assertTrue(PasswordChangeRequired.matches(403, """{"code":"password_change_required"}"""))
    }

    @Test
    fun `the same body with another status is not a forced password change`() {
        assertFalse(PasswordChangeRequired.matches(401, nested))
        assertFalse(PasswordChangeRequired.matches(200, nested))
    }

    @Test
    fun `a generic 403 is not a forced password change`() {
        listOf(
            """{"detail":"Forbidden"}""",
            """{"detail":{"code":"other_code"}}""",
            """{"code":403}""",
            """{"detail":{"code":123}}""",
            """["password_change_required"]""",
            "not json password_change_required",
            "",
        ).forEach { assertFalse("body '$it'", PasswordChangeRequired.matches(403, it)) }
        assertFalse(PasswordChangeRequired.matches(403, null))
    }

    @Test
    fun `user message points to the web change-password page and carries no server text`() {
        assertTrue(PasswordChangeRequired.USER_MESSAGE.contains("change your password"))
        assertTrue(PasswordChangeRequired.USER_MESSAGE.contains("web"))
        assertEquals(PasswordChangeRequired.USER_MESSAGE, ApiException.PasswordChangeRequired().message)
        assertFalse(ApiException.PasswordChangeRequired().retryable)
    }
}

class PasswordChangeRequiredHttpTest {
    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun api() = HttpApi(OkHttpClient(), { server.url("/").toString().trimEnd('/') }, { "test-token-not-real" })

    private fun forbidden(body: String) =
        MockResponse().setResponseCode(403).setHeader("Content-Type", "application/json").setBody(body)

    private fun expectPasswordChange(block: suspend () -> Unit) {
        try {
            runBlocking { block() }
        } catch (e: ApiException) {
            assertTrue("unexpected ${e::class}", e is ApiException.PasswordChangeRequired)
            return
        }
        fail("expected PasswordChangeRequired")
    }

    @Test
    fun `login answered with the 403 code is a password change, not a bad password`() {
        server.enqueue(forbidden("""{"detail":{"code":"password_change_required","message":"x"}}"""))

        expectPasswordChange { api().login("fakeuser", "fake-password") }
    }

    @Test
    fun `pull and push answered with the 403 code are password changes`() {
        server.enqueue(forbidden("""{"code":"password_change_required"}"""))
        server.enqueue(forbidden("""{"detail":{"code":"password_change_required"}}"""))

        expectPasswordChange { api().pull(0, 10) }
        expectPasswordChange { api().push(app.nathavaram.voters.sync.PushRequest("android-test", emptyList())) }
    }

    @Test
    fun `any other 403 stays a plain non-retryable http error`() {
        server.enqueue(forbidden("""{"detail":"Forbidden"}"""))

        try {
            runBlocking { api().pull(0, 10) }
            fail("expected ApiException")
        } catch (e: ApiException.Http) {
            assertEquals(403, e.code)
            assertFalse(e.retryable)
        }
    }
}

package app.nathavaram.voters.ui

import app.nathavaram.voters.net.ApiException
import app.nathavaram.voters.net.PasswordChangeRequired
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class ErrorMessagesTest {
    @Test
    fun `forced password change tells the user to change it on the web`() {
        val message = ErrorMessages.forSignIn(ApiException.PasswordChangeRequired())

        assertEquals(PasswordChangeRequired.USER_MESSAGE, message)
        assertTrue(message.orEmpty().contains("web"))
        assertTrue(message.orEmpty().contains("change-password"))
    }

    @Test
    fun `search screen notice uses the same message`() {
        assertEquals(PasswordChangeRequired.USER_MESSAGE, ErrorMessages.PASSWORD_CHANGE_NOTICE)
    }

    @Test
    fun `other sign-in failures keep their existing messages`() {
        assertEquals("Invalid username or password", ErrorMessages.forSignIn(ApiException.InvalidCredentials()))
        assertEquals("Server returned HTTP 403", ErrorMessages.forSignIn(ApiException.Http(403)))
        assertEquals("Cannot reach the server", ErrorMessages.forSignIn(IOException("down")))
        assertNull(ErrorMessages.forSignIn(IllegalStateException("x")))
    }
}

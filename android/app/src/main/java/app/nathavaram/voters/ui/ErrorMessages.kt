package app.nathavaram.voters.ui

import app.nathavaram.voters.net.ApiException
import app.nathavaram.voters.net.PasswordChangeRequired
import java.io.IOException

/** User-facing text for sign-in failures. Pure, so it is unit-testable without Android. */
object ErrorMessages {
    const val SIGN_IN_AGAIN = "Sign in again"
    const val CANNOT_REACH = "Cannot reach the server"

    /** Text shown on the search screen while a forced password change is pending. */
    const val PASSWORD_CHANGE_NOTICE = PasswordChangeRequired.USER_MESSAGE

    fun forSignIn(error: Exception): String? = when (error) {
        is ApiException.PasswordChangeRequired -> PASSWORD_CHANGE_NOTICE
        is ApiException -> error.message
        is IOException -> CANNOT_REACH
        else -> null
    }
}

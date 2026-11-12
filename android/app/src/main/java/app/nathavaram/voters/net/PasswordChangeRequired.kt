package app.nathavaram.voters.net

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Recognises the server's forced-password-change response: HTTP 403 whose JSON carries the code
 * `password_change_required`, either at `detail.code` (FastAPI default) or top-level `code`.
 * Any other 403 is not a forced change. Only the code is read; the body is never kept or logged.
 */
object PasswordChangeRequired {
    const val CODE = "password_change_required"

    /** Shown on the login screen and the search screen. Never includes server text. */
    const val USER_MESSAGE =
        "You must change your password before you can sync. " +
            "Open the web app in a browser, sign in, and change your password on the change-password page. " +
            "Then come back here and sign in again."

    fun matches(status: Int, body: String?): Boolean = status == 403 && hasCode(body)

    private fun hasCode(body: String?): Boolean {
        if (body.isNullOrBlank()) return false
        val root = try {
            Json.parseToJsonElement(body) as? JsonObject
        } catch (e: IllegalArgumentException) {
            null
        } ?: return false
        if (codeOf(root) == CODE) return true
        return codeOf(root["detail"] as? JsonObject) == CODE
    }

    private fun codeOf(obj: JsonObject?): String? =
        (obj?.get("code") as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull
}

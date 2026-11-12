package app.nathavaram.voters.domain

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Validates the operator-entered server address. A fresh install uses [DEFAULT]; an address saved in
 * Settings always wins over it.
 */
object BaseUrl {
    /** Built-in server address used until the operator saves a different one. No trailing slash. */
    const val DEFAULT = "https://nathavaram.duckdns.org"

    sealed interface Result {
        data class Ok(val url: String) : Result
        data class Invalid(val reason: String) : Result
    }

    fun normalize(input: String, allowCleartext: Boolean): Result {
        val trimmed = input.trim().trimEnd('/')
        if (trimmed.isEmpty()) return Result.Invalid("Enter the server address")
        val url = trimmed.toHttpUrlOrNull()
            ?: return Result.Invalid("Enter a full address such as https://host")
        if (url.query != null || url.fragment != null) {
            return Result.Invalid("The address must not contain a query or fragment")
        }
        if (!url.isHttps && !allowCleartext) {
            return Result.Invalid("HTTPS is required")
        }
        return Result.Ok(trimmed)
    }
}

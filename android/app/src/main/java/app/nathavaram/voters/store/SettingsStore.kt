package app.nathavaram.voters.store

import android.content.Context
import android.content.SharedPreferences
import app.nathavaram.voters.domain.BaseUrl
import java.util.UUID

/** Non-secret settings: server address, device id, last sync state. The token lives in TokenStore. */
class SettingsStore(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /**
     * The server address in effect: the saved override if any, otherwise [BaseUrl.DEFAULT].
     * The default is never persisted, so it cannot mask a later change to the built-in value.
     * Login, sync and Settings all read this one property.
     */
    var baseUrl: String
        get() = prefs.getString(KEY_BASE_URL, null)?.takeIf { it.isNotBlank() } ?: BaseUrl.DEFAULT
        set(value) = prefs.edit().putString(KEY_BASE_URL, value).apply()

    var clockSkew: Boolean
        get() = prefs.getBoolean(KEY_CLOCK_SKEW, false)
        set(value) = prefs.edit().putBoolean(KEY_CLOCK_SKEW, value).apply()

    var lastSyncAt: String?
        get() = prefs.getString(KEY_LAST_SYNC, null)
        set(value) = prefs.edit().putString(KEY_LAST_SYNC, value).apply()

    /** Set when the server answered 403 password_change_required; cleared by a successful sync or sign-in. */
    var passwordChangeRequired: Boolean
        get() = prefs.getBoolean(KEY_PASSWORD_CHANGE, false)
        set(value) = prefs.edit().putBoolean(KEY_PASSWORD_CHANGE, value).apply()

    /**
     * The role the server reported at sign-in ("admin" or "user"); blank when unknown, for example
     * after an app update that predates this setting. Unknown is treated as a regular user, so the
     * restricted fields stay read-only until the next sign-in. The server enforces the rule either way.
     */
    var role: String
        get() = prefs.getString(KEY_ROLE, null).orEmpty()
        set(value) = prefs.edit().putString(KEY_ROLE, value).apply()

    /** Owner/admin may edit every field except VoterId; the regular user role only Party, Caste, Mobile, Address. */
    val canEditAllFields: Boolean get() = role == ROLE_ADMIN

    /** Random, generated once per install. Sent as `device_id` and stored as `source_id` by the server. */
    val deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, null) ?: newDeviceId().also {
            prefs.edit().putString(KEY_DEVICE_ID, it).apply()
        }

    private fun newDeviceId() = "android-" + UUID.randomUUID().toString().take(DEVICE_ID_CHARS)

    private companion object {
        const val FILE = "settings"
        const val KEY_BASE_URL = "base_url"
        const val KEY_CLOCK_SKEW = "clock_skew"
        const val KEY_LAST_SYNC = "last_sync_at"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_PASSWORD_CHANGE = "password_change_required"
        const val KEY_ROLE = "role"
        const val ROLE_ADMIN = "admin"
        const val DEVICE_ID_CHARS = 8
    }
}

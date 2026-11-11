package app.nathavaram.voters.store

import android.content.Context
import android.content.SharedPreferences
import java.util.UUID

/** Non-secret settings: server address, device id, last sync state. The token lives in TokenStore. */
class SettingsStore(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var baseUrl: String?
        get() = prefs.getString(KEY_BASE_URL, null)
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
        const val DEVICE_ID_CHARS = 8
    }
}

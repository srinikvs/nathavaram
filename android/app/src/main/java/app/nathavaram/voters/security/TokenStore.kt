package app.nathavaram.voters.security

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Holds the Bearer JWT. The token is never logged. */
interface TokenStore {
    fun token(): String?
    fun username(): String?
    fun save(token: String, username: String)
    fun clear()
}

/** JWT kept in EncryptedSharedPreferences (AES-256 keys held in the Android Keystore). */
class EncryptedTokenStore(context: Context) : TokenStore {
    private val prefs: SharedPreferences = run {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context,
            FILE,
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override fun token(): String? = prefs.getString(KEY_TOKEN, null)

    override fun username(): String? = prefs.getString(KEY_USERNAME, null)

    override fun save(token: String, username: String) {
        prefs.edit().putString(KEY_TOKEN, token).putString(KEY_USERNAME, username).apply()
    }

    override fun clear() {
        prefs.edit().remove(KEY_TOKEN).remove(KEY_USERNAME).apply()
    }

    private companion object {
        const val FILE = "secure_session"
        const val KEY_TOKEN = "jwt"
        const val KEY_USERNAME = "username"
    }
}

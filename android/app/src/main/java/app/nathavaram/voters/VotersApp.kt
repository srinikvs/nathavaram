package app.nathavaram.voters

import android.app.Application
import androidx.room.Room
import app.nathavaram.voters.net.HttpApi
import app.nathavaram.voters.security.EncryptedTokenStore
import app.nathavaram.voters.security.TokenStore
import app.nathavaram.voters.store.AppDatabase
import app.nathavaram.voters.store.SettingsStore
import app.nathavaram.voters.store.VoterRepository
import app.nathavaram.voters.sync.SyncEngine
import app.nathavaram.voters.sync.SyncScheduler
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/** Hand-wired dependencies. The HTTP client has no logging interceptor and no cookie jar. */
class AppContainer(private val app: Application) {
    val settings: SettingsStore by lazy { SettingsStore(app) }
    val tokens: TokenStore by lazy { EncryptedTokenStore(app) }
    val database: AppDatabase by lazy {
        Room.databaseBuilder(app, AppDatabase::class.java, "voters-local").build()
    }

    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    val api: HttpApi by lazy { HttpApi(http, { settings.baseUrl }, { tokens.token() }) }

    val repository: VoterRepository by lazy {
        VoterRepository(database, onChanged = { SyncScheduler.requestSync(app) })
    }

    fun newSyncEngine(): SyncEngine = SyncEngine(database, api, settings.deviceId)
}

class VotersApp : Application() {
    val container: AppContainer by lazy { AppContainer(this) }

    override fun onCreate() {
        super.onCreate()
        SyncScheduler.schedulePeriodic(this)
    }
}

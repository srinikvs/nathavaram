package app.nathavaram.voters.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import app.nathavaram.voters.VotersApp
import app.nathavaram.voters.domain.Timestamps
import app.nathavaram.voters.net.ApiException
import java.io.IOException

/** Runs one push-then-pull cycle. Nothing is logged: no token, no voter data. */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val container = (applicationContext as VotersApp).container
        if (container.tokens.token() == null || container.settings.baseUrl == null) return Result.success()
        return try {
            val summary = container.newSyncEngine().run()
            container.settings.clockSkew = summary.clockSkew
            container.settings.lastSyncAt = Timestamps.now()
            Result.success()
        } catch (e: ApiException.Unauthorized) {
            container.tokens.clear()
            Result.failure()
        } catch (e: ApiException) {
            if (e.retryable) Result.retry() else Result.failure()
        } catch (e: IOException) {
            Result.retry()
        }
    }
}

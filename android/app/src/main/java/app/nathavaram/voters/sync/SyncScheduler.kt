package app.nathavaram.voters.sync

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/** Queues sync work that runs once the device is connected, retrying with backoff. */
object SyncScheduler {
    private const val ONE_TIME = "voter-sync"
    private const val PERIODIC = "voter-sync-periodic"
    private const val BACKOFF_SECONDS = 30L
    private const val PERIOD_MINUTES = 15L

    private val connected = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /** Call after every local save and on demand. Edits made offline wait until there is a network. */
    fun requestSync(context: Context) {
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(connected)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context)
            .enqueueUniqueWork(ONE_TIME, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
    }

    fun schedulePeriodic(context: Context) {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(PERIOD_MINUTES, TimeUnit.MINUTES)
            .setConstraints(connected)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context)
            .enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP, request)
    }
}

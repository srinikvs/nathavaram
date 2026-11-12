package app.nathavaram.voters.ui

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/** Cheap "is there a network at all" check used only to decide whether to show the offline message. */
object Connectivity {
    fun isOnline(context: Context): Boolean {
        val manager = context.getSystemService(ConnectivityManager::class.java) ?: return false
        val network = manager.activeNetwork ?: return false
        val caps = manager.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}

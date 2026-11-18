package app.nathavaram.voters.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.nathavaram.voters.store.ConflictEntity
import app.nathavaram.voters.sync.ConflictDiff
import app.nathavaram.voters.sync.ResultStatus

@Composable
fun ConflictsScreen(vm: AppViewModel) {
    val conflicts by vm.conflicts.collectAsState()

    Column(modifier = Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Needs review", style = MaterialTheme.typography.headlineSmall)
        TextButton(onClick = { vm.go(Screen.Search) }) { Text("Back") }
        if (conflicts.isEmpty()) Text("Nothing to review.")
        LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            items(conflicts, key = { it.voterId }) { conflict ->
                ConflictCard(conflict, onDismiss = { vm.dismissConflict(conflict.voterId) })
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun ConflictCard(conflict: ConflictEntity, onDismiss: () -> Unit) {
    val local = ConflictDiff.decode(conflict.localJson)
    val server = ConflictDiff.decode(conflict.serverJson)
    val diffs = ConflictDiff.diff(local, server)

    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(headline(conflict.kind), style = MaterialTheme.typography.titleMedium)
        conflict.message?.let { Text(it) }
        if (server?.deletedAt != null) Text("The server record was removed.")
        diffs.forEach { d ->
            Text("${d.label}: yours “${d.local ?: "-"}” → server “${d.server ?: "-"}”")
        }
        TextButton(onClick = onDismiss) { Text("Dismiss") }
    }
}

private fun headline(kind: String): String = when (kind) {
    ResultStatus.CONFLICT -> "Changed on the server — the server version was kept"
    ResultStatus.FORBIDDEN -> "Your role cannot change this — the server version was kept"
    ResultStatus.VALIDATION_ERROR -> "The server rejected this edit — fix it and save again"
    else -> "Sync note"
}

package app.nathavaram.voters.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SearchScreen(vm: AppViewModel) {
    val query by vm.query.collectAsState()
    val results by vm.results.collectAsState()
    val pending by vm.pendingCount.collectAsState()
    val conflicts by vm.conflicts.collectAsState()
    val snackbarHost = remember { SnackbarHostState() }

    LaunchedEffect(vm) {
        vm.messages.collect { snackbarHost.showSnackbar(it) }
    }

    Box(modifier = Modifier.fillMaxSize()) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Voters", style = MaterialTheme.typography.headlineSmall)
        vm.passwordChangeNotice?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (vm.clockSkew) {
            Text(
                "This phone's clock differs from the server by more than 5 minutes. Fix the date and time before editing.",
                color = MaterialTheme.colorScheme.error,
            )
        }
        OutlinedTextField(
            value = query,
            onValueChange = { vm.query.value = it },
            label = { Text("Search name, VoterId, house no, mobile") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        // FlowRow wraps instead of scrolling, so Sync and Sign out are always on screen.
        FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Button(onClick = { vm.go(Screen.Edit(null)) }) { Text("Add") }
            TextButton(onClick = vm::syncNow) { Text(SyncLabel.forPending(pending)) }
            TextButton(onClick = { vm.go(Screen.Conflicts) }) { Text("Review (${conflicts.size})") }
            TextButton(onClick = { vm.go(Screen.Settings) }) { Text("Server") }
            TextButton(onClick = vm::signOut) { Text("Sign out") }
        }
        if (results.isEmpty()) {
            Text("No matching voters on this phone.")
        }
        LazyColumn(contentPadding = PaddingValues(vertical = 4.dp)) {
            items(results, key = { it.id }) { voter ->
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { vm.go(Screen.Edit(voter.id)) }
                        .padding(vertical = 8.dp),
                ) {
                    val name = listOfNotNull(voter.firstName, voter.surname).joinToString(" ")
                    Text(name, style = MaterialTheme.typography.titleMedium)
                    val place = listOfNotNull(voter.village, voter.address).joinToString(", ").ifEmpty { "-" }
                    Text("${voter.voterId} · $place" + if (voter.dirty) " · not synced" else "")
                }
                HorizontalDivider()
            }
        }
    }
    SnackbarHost(snackbarHost, modifier = Modifier.align(Alignment.BottomCenter))
    }
}

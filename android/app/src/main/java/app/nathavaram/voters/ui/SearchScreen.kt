package app.nathavaram.voters.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun SearchScreen(vm: AppViewModel) {
    val query by vm.query.collectAsState()
    val results by vm.results.collectAsState()
    val pending by vm.pendingCount.collectAsState()
    val conflicts by vm.conflicts.collectAsState()

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
        Row(modifier = Modifier.horizontalScroll(rememberScrollState())) {
            Button(onClick = { vm.go(Screen.Edit(null)) }) { Text("Add") }
            TextButton(onClick = vm::syncNow) { Text(if (pending > 0) "Sync ($pending waiting)" else "Sync") }
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
                    Text("${voter.voterId} · house ${voter.houseNo ?: "-"}" + if (voter.dirty) " · not synced" else "")
                }
                HorizontalDivider()
            }
        }
    }
}

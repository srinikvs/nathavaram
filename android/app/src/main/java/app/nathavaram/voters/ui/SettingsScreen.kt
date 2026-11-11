package app.nathavaram.voters.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp

@Composable
fun SettingsScreen(vm: AppViewModel) {
    var url by remember { mutableStateOf(vm.baseUrl) }
    var error by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
    ) {
        Text("Server address", style = MaterialTheme.typography.headlineMedium)
        Text("The address of your Nathavaram server, for example https://your-server.example")
        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("Server address") },
            singleLine = true,
            isError = error != null,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(onClick = { error = vm.saveBaseUrl(url) }, modifier = Modifier.fillMaxWidth()) {
            Text("Save")
        }
        if (vm.baseUrl.isNotEmpty()) {
            TextButton(onClick = { vm.go(Screen.Search) }) { Text("Cancel") }
        }
    }
}

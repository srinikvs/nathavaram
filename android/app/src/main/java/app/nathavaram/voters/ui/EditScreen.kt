package app.nathavaram.voters.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import app.nathavaram.voters.store.SaveResult
import app.nathavaram.voters.domain.FieldKey
import app.nathavaram.voters.domain.VoterForm
import app.nathavaram.voters.domain.Vocab
import kotlinx.coroutines.launch

private data class TextField(val key: String, val label: String, val keyboard: KeyboardType = KeyboardType.Text)

private val BEFORE_GENDER = listOf(
    TextField(FieldKey.VOTER_ID, "VoterId"),
    TextField(FieldKey.FIRST_NAME, "FirstName"),
    TextField(FieldKey.SURNAME, "SurName"),
    TextField(FieldKey.RELATIVE_NAME, "RelativeName"),
    TextField(FieldKey.HOUSE_NO, "HouseNo"),
    TextField(FieldKey.BOOTH, "Booth"),
    TextField(FieldKey.BS_NO_2024, "2024 B.S.No"),
    TextField(FieldKey.WARD, "Ward"),
    TextField(FieldKey.WARD_S_NO, "Ward S No"),
)
private val AFTER_GENDER = listOf(
    TextField(FieldKey.AGE, "Age", KeyboardType.Number),
    TextField(FieldKey.CASTE, "Caste"),
    TextField(FieldKey.MOBILE, "Mobile", KeyboardType.Phone),
    TextField(FieldKey.C_CITY, "C_City"),
    TextField(FieldKey.PROFESSION, "Profession"),
)

private const val NO_ANSWER = "(no answer)"

/** Saves the in-progress form (or "not loaded yet") across configuration changes; never written to disk. */
private val NullableFormSaver: Saver<VoterForm?, Any> = Saver(
    save = { it?.toSavedList()?.let(::ArrayList) ?: NOT_LOADED },
    restore = { saved -> (saved as? List<*>)?.let(VoterForm::fromSavedList) },
)

private const val NOT_LOADED = "not-loaded"

@Composable
fun EditScreen(vm: AppViewModel, id: String?) {
    var form by rememberSaveable(id, stateSaver = NullableFormSaver) { mutableStateOf<VoterForm?>(null) }
    var errors by remember(id) { mutableStateOf<Map<String, String>>(emptyMap()) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(id) {
        // After rotation the restored form already holds the operator's typed text; do not overwrite it.
        if (form != null) return@LaunchedEffect
        val loaded = vm.formFor(id)
        if (loaded == null) vm.go(Screen.Search) else form = loaded
    }

    val current = form ?: return

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(if (id == null) "Add voter" else "Edit voter", style = MaterialTheme.typography.headlineSmall)
        // VoterId is the unique key: typed once when adding, read-only text when editing.
        BEFORE_GENDER.forEach { f ->
            Field(f, current, errors, readOnly = id != null && f.key == FieldKey.VOTER_ID) { form = it }
        }
        Dropdown(
            label = "Gender",
            value = current.gender,
            options = Vocab.GENDERS,
            error = errors[FieldKey.GENDER],
        ) { form = current.with(FieldKey.GENDER, it) }
        AFTER_GENDER.forEach { f -> Field(f, current, errors) { form = it } }
        Dropdown(
            label = "Party",
            value = current.party,
            options = listOf("") + Vocab.PARTIES,
            error = errors[FieldKey.PARTY],
        ) { form = current.with(FieldKey.PARTY, it) }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                scope.launch {
                    when (val result = vm.save(id, current)) {
                        is SaveResult.Invalid -> errors = result.errors
                        is SaveResult.Saved -> vm.go(Screen.Search)
                    }
                }
            }) { Text("Save") }
            TextButton(onClick = { vm.go(Screen.Search) }) { Text("Cancel") }
        }
    }
}

@Composable
private fun Field(
    spec: TextField,
    form: VoterForm,
    errors: Map<String, String>,
    readOnly: Boolean = false,
    onChange: (VoterForm) -> Unit,
) {
    OutlinedTextField(
        value = form.get(spec.key),
        onValueChange = { if (!readOnly) onChange(form.with(spec.key, it)) },
        readOnly = readOnly,
        label = { Text(spec.label) },
        singleLine = true,
        isError = spec.key in errors,
        supportingText = (errors[spec.key] ?: if (readOnly) "VoterId cannot be changed" else null)
            ?.let { { Text(it) } },
        keyboardOptions = KeyboardOptions(keyboardType = spec.keyboard),
        modifier = Modifier.fillMaxWidth(),
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Dropdown(
    label: String,
    value: String,
    options: List<String>,
    error: String?,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
        OutlinedTextField(
            value = value.ifEmpty { NO_ANSWER },
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            isError = error != null,
            supportingText = error?.let { { Text(it) } },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option.ifEmpty { NO_ANSWER }) },
                    onClick = {
                        onSelect(option)
                        expanded = false
                    },
                )
            }
        }
    }
}

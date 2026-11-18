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

/** Text inputs in the order of the voter list. Local/Non-Local and Party are drop-downs placed in between. */
private val BEFORE_LOCAL = listOf(
    TextField(FieldKey.BOOTH, "Booth"),
    TextField(FieldKey.BOOTH_S_NO, "Booth S.No"),
    TextField(FieldKey.WARD, "Ward"),
    TextField(FieldKey.WARD_S_NO, "Ward S.No"),
    TextField(FieldKey.VOTER_ID, "VoterId"),
    TextField(FieldKey.FIRST_NAME, "FirstName"),
    TextField(FieldKey.LAST_NAME, "LastName"),
    TextField(FieldKey.RELATIVE_NAME, "RelativeName"),
    TextField(FieldKey.HOUSE_NO, "HouseNo"),
    TextField(FieldKey.AGE, "Age", KeyboardType.Number),
    TextField(FieldKey.STREET, "Street"),
    TextField(FieldKey.MOBILE, "Mobile", KeyboardType.Phone),
    TextField(FieldKey.CASTE, "Caste"),
    TextField(FieldKey.VILLAGE, "Village"),
    TextField(FieldKey.PANCHAYATH, "Panchayath"),
    TextField(FieldKey.MANDAL, "Mandal"),
    TextField(FieldKey.CURRENT_CITY, "Current City"),
)

private const val NO_ANSWER = "(no answer)"
private const val READ_ONLY_HINT = "Only an owner or admin can change this"
private const val RESTRICTED_NOTICE =
    "You can edit Party, Caste, Mobile, HouseNo and Street. Other fields are read-only; ask an owner or admin to change them."

/** Saves the in-progress form (or "not loaded yet") across configuration changes; never written to disk. */
private val NullableFormSaver: Saver<VoterForm?, Any> = Saver(
    save = { it?.toSavedList()?.let(::ArrayList) ?: NOT_LOADED },
    restore = { saved -> (saved as? List<*>)?.let(VoterForm::fromSavedList) },
)

private const val NOT_LOADED = "not-loaded"

@Composable
fun EditScreen(vm: AppViewModel, voterId: String?) {
    var form by rememberSaveable(voterId, stateSaver = NullableFormSaver) { mutableStateOf<VoterForm?>(null) }
    var errors by remember(voterId) { mutableStateOf<Map<String, String>>(emptyMap()) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(voterId) {
        // After rotation the restored form already holds the operator's typed text; do not overwrite it.
        if (form != null) return@LaunchedEffect
        val loaded = vm.formFor(voterId)
        if (loaded == null) vm.go(Screen.Search) else form = loaded
    }

    val current = form ?: return

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(if (voterId == null) "Add voter" else "Edit voter", style = MaterialTheme.typography.headlineSmall)
        // VoterId is the unique key: typed once when adding, read-only text when editing.
        // On an existing voter a regular user may edit only Party, Caste, Mobile, HouseNo and Street; the
        // other fields stay visible but read-only. Owner/admin may edit everything but VoterId.
        val restricted = voterId != null && !vm.canEditAllFields
        fun lockedByRole(key: String) = restricted && key !in FieldKey.USER_EDITABLE
        if (restricted) {
            Text(RESTRICTED_NOTICE, style = MaterialTheme.typography.bodyMedium)
        }
        BEFORE_LOCAL.forEach { f ->
            val idLocked = voterId != null && f.key == FieldKey.VOTER_ID
            Field(f, current, errors, readOnly = idLocked || lockedByRole(f.key), idLocked = idLocked) { form = it }
        }
        Dropdown(
            label = "Local/Non-Local",
            value = current.isLocal,
            options = Vocab.LOCAL_OPTIONS,
            error = errors[FieldKey.IS_LOCAL],
            locked = lockedByRole(FieldKey.IS_LOCAL),
        ) { form = current.with(FieldKey.IS_LOCAL, it) }
        Dropdown(
            label = "Party",
            value = current.party,
            options = listOf("") + Vocab.PARTIES,
            error = errors[FieldKey.PARTY],
            locked = lockedByRole(FieldKey.PARTY),
        ) { form = current.with(FieldKey.PARTY, it) }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                scope.launch {
                    when (val result = vm.save(voterId, current)) {
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
    idLocked: Boolean = false,
    onChange: (VoterForm) -> Unit,
) {
    OutlinedTextField(
        value = form.get(spec.key),
        onValueChange = { if (!readOnly) onChange(form.with(spec.key, it)) },
        readOnly = readOnly,
        label = { Text(spec.label) },
        singleLine = true,
        isError = spec.key in errors,
        supportingText = (
            errors[spec.key] ?: when {
                idLocked -> "VoterId cannot be changed"
                readOnly -> READ_ONLY_HINT
                else -> null
            }
            )?.let { { Text(it) } },
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
    locked: Boolean = false,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    // A locked dropdown shows its value but never opens, so the role rule cannot be bypassed by tapping.
    ExposedDropdownMenuBox(expanded = expanded && !locked, onExpandedChange = { expanded = it && !locked }) {
        OutlinedTextField(
            value = value.ifEmpty { NO_ANSWER },
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            isError = error != null,
            supportingText = (error ?: if (locked) READ_ONLY_HINT else null)?.let { { Text(it) } },
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

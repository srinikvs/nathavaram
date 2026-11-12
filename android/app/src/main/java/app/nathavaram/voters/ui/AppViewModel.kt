package app.nathavaram.voters.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.nathavaram.voters.BuildConfig
import app.nathavaram.voters.VotersApp
import app.nathavaram.voters.store.ConflictEntity
import app.nathavaram.voters.store.SaveResult
import app.nathavaram.voters.store.VoterEntity
import app.nathavaram.voters.domain.BaseUrl
import app.nathavaram.voters.domain.VoterForm
import app.nathavaram.voters.net.ApiException
import app.nathavaram.voters.sync.SyncScheduler
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.io.IOException

sealed interface Screen {
    data object Login : Screen
    data object Settings : Screen
    data object Search : Screen
    data class Edit(val id: String?) : Screen
    data object Conflicts : Screen
}

data class LoginUi(val busy: Boolean = false, val error: String? = null)

class AppViewModel(application: Application) : AndroidViewModel(application) {
    private val container = (application as VotersApp).container
    private val repository = container.repository
    private val settings = container.settings

    private val _screen = MutableStateFlow(initialScreen())
    val screen: StateFlow<Screen> = _screen.asStateFlow()

    private val _login = MutableStateFlow(LoginUi())
    val login: StateFlow<LoginUi> = _login.asStateFlow()

    /** One-shot user messages (shown as a snackbar). Buffered so a tap during rotation is not lost. */
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val messages: SharedFlow<String> = _messages.asSharedFlow()

    val query = MutableStateFlow("")

    @OptIn(FlowPreview::class, ExperimentalCoroutinesApi::class)
    val results: StateFlow<List<VoterEntity>> = query
        .debounce(SEARCH_DEBOUNCE_MS)
        .flatMapLatest { repository.search(it) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS), emptyList())

    val pendingCount: StateFlow<Int> = container.database.voterDao().dirtyCount()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS), 0)

    val conflicts: StateFlow<List<ConflictEntity>> = container.database.conflictDao().all()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS), emptyList())

    val baseUrl: String get() = settings.baseUrl
    val clockSkew: Boolean get() = settings.clockSkew

    /** Owner/admin: every field but VoterId is editable. Otherwise only Party, Caste, Mobile and Address. */
    val canEditAllFields: Boolean get() = settings.canEditAllFields

    /** Non-null while the server is demanding a password change (done on the web). */
    val passwordChangeNotice: String?
        get() = if (settings.passwordChangeRequired) ErrorMessages.PASSWORD_CHANGE_NOTICE else null

    init {
        viewModelScope.launch {
            container.session.expired.collect {
                _login.value = LoginUi(error = ErrorMessages.SIGN_IN_AGAIN)
                _screen.value = Screen.Login
            }
        }
    }

    private fun initialScreen(): Screen =
        if (container.tokens.token() == null) Screen.Login else Screen.Search

    fun go(screen: Screen) {
        _screen.value = screen
    }

    /** Returns an error message, or null when the address was saved. */
    fun saveBaseUrl(input: String): String? =
        when (val r = BaseUrl.normalize(input, allowCleartext = BuildConfig.DEBUG)) {
            is BaseUrl.Result.Invalid -> r.reason
            is BaseUrl.Result.Ok -> {
                settings.baseUrl = r.url
                _screen.value = if (container.tokens.token() == null) Screen.Login else Screen.Search
                null
            }
        }

    fun signIn(username: String, password: String) {
        if (username.isBlank() || password.isEmpty()) {
            _login.value = LoginUi(error = "Enter your username and password")
            return
        }
        _login.value = LoginUi(busy = true)
        viewModelScope.launch {
            _login.value = try {
                val response = container.api.login(username.trim(), password)
                container.tokens.save(response.accessToken, response.username.ifEmpty { username.trim() })
                settings.passwordChangeRequired = false
                settings.role = response.role
                SyncScheduler.requestSync(getApplication())
                _screen.value = Screen.Search
                LoginUi()
            } catch (e: ApiException.PasswordChangeRequired) {
                settings.passwordChangeRequired = true
                LoginUi(error = ErrorMessages.forSignIn(e))
            } catch (e: ApiException) {
                LoginUi(error = ErrorMessages.forSignIn(e))
            } catch (e: IOException) {
                LoginUi(error = ErrorMessages.forSignIn(e))
            }
        }
    }

    fun signOut() {
        container.tokens.clear()
        settings.passwordChangeRequired = false
        settings.role = ""
        _screen.value = Screen.Login
    }

    fun cancelSettings() {
        _screen.value = if (container.tokens.token() == null) Screen.Login else Screen.Search
    }

    /** Always queues the sync; also tells the operator when the phone is offline. */
    fun syncNow() {
        SyncScheduler.requestSync(getApplication())
        if (!Connectivity.isOnline(getApplication())) {
            _messages.tryEmit(ErrorMessages.OFFLINE_SYNC)
        }
    }

    suspend fun formFor(id: String?): VoterForm? =
        if (id == null) VoterForm() else repository.get(id)?.let(VoterForm::from)

    suspend fun save(id: String?, form: VoterForm): SaveResult = repository.save(id, form)

    fun dismissConflict(id: String) {
        viewModelScope.launch { container.database.conflictDao().delete(id) }
    }

    private companion object {
        const val SEARCH_DEBOUNCE_MS = 150L
        const val STOP_TIMEOUT_MS = 5_000L
    }
}

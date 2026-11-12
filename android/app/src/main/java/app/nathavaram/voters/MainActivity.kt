package app.nathavaram.voters

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import app.nathavaram.voters.ui.AppViewModel
import app.nathavaram.voters.ui.ConflictsScreen
import app.nathavaram.voters.ui.EditScreen
import app.nathavaram.voters.ui.LoginScreen
import app.nathavaram.voters.ui.Screen
import app.nathavaram.voters.ui.SearchScreen
import app.nathavaram.voters.ui.SettingsScreen

class MainActivity : ComponentActivity() {
    private val viewModel: AppViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface {
                    val screen by viewModel.screen.collectAsState()
                    BackHandler(enabled = screen is Screen.Edit || screen is Screen.Conflicts) {
                        viewModel.go(Screen.Search)
                    }
                    when (val current = screen) {
                        Screen.Login -> LoginScreen(viewModel)
                        Screen.Settings -> SettingsScreen(viewModel)
                        Screen.Search -> SearchScreen(viewModel)
                        is Screen.Edit -> EditScreen(viewModel, current.id)
                        Screen.Conflicts -> ConflictsScreen(viewModel)
                    }
                }
            }
        }
    }
}

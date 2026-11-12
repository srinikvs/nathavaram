package app.nathavaram.voters.net

import app.nathavaram.voters.sync.LoginRequest
import app.nathavaram.voters.sync.LoginResponse
import app.nathavaram.voters.sync.PullResponse
import app.nathavaram.voters.sync.PushRequest
import app.nathavaram.voters.sync.PushResponse
import app.nathavaram.voters.sync.SyncApi
import app.nathavaram.voters.sync.SyncJson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Failures carry fixed messages only: never a response body, token, password or voter data. */
sealed class ApiException(message: String, val retryable: Boolean) : Exception(message) {
    class NotConfigured : ApiException("Server address is not set", retryable = false)
    class Unauthorized : ApiException("Sign in again", retryable = false)
    class InvalidCredentials : ApiException("Invalid username or password", retryable = false)
    class PasswordChangeRequired : ApiException(app.nathavaram.voters.net.PasswordChangeRequired.USER_MESSAGE, retryable = false)
    class Http(val code: Int) : ApiException("Server returned HTTP $code", retryable = code >= 500)
    class Malformed : ApiException("Unexpected response from server", retryable = false)
}

interface AuthApi {
    suspend fun login(username: String, password: String): LoginResponse
}

/**
 * Sync and login calls. Sync calls send `Authorization: Bearer <JWT>` and nothing else for
 * authentication: no cookie jar, no CSRF header. Only the sync and login endpoints are used,
 * so there is no export, import or bulk delete call in this client.
 */
class HttpApi(
    private val client: OkHttpClient,
    private val baseUrl: () -> String?,
    private val token: () -> String?,
) : SyncApi, AuthApi {

    override suspend fun login(username: String, password: String): LoginResponse {
        val body = SyncJson.json
            .encodeToString(LoginRequest.serializer(), LoginRequest(username, password))
            .toRequestBody(JSON)
        val request = Request.Builder().url(url("api/auth/login")).post(body).build()
        val response = execute(request, sentBearer = false) {
            SyncJson.json.decodeFromString(LoginResponse.serializer(), it)
        }
        if (response.accessToken.isEmpty()) throw ApiException.Malformed()
        return response
    }

    override suspend fun pull(since: Long, limit: Int): PullResponse {
        val url = url("api/sync/pull").newBuilder()
            .addQueryParameter("since", since.toString())
            .addQueryParameter("limit", limit.toString())
            .build()
        return execute(authed(Request.Builder().url(url).get()), sentBearer = true) {
            SyncJson.json.decodeFromString(PullResponse.serializer(), it)
        }
    }

    override suspend fun push(request: PushRequest): PushResponse {
        val body = SyncJson.json.encodeToString(PushRequest.serializer(), request).toRequestBody(JSON)
        val http = authed(Request.Builder().url(url("api/sync/push")).post(body))
        return execute(http, sentBearer = true) {
            SyncJson.json.decodeFromString(PushResponse.serializer(), it)
        }
    }

    private fun url(path: String): HttpUrl {
        val base = baseUrl()?.toHttpUrlOrNull() ?: throw ApiException.NotConfigured()
        return base.newBuilder().addPathSegments(path).build()
    }

    private fun authed(builder: Request.Builder): Request {
        val jwt = token() ?: throw ApiException.Unauthorized()
        return builder.header("Authorization", "Bearer $jwt").build()
    }

    private suspend fun <T> execute(request: Request, sentBearer: Boolean, parse: (String) -> T): T =
        withContext(Dispatchers.IO) {
            client.newCall(request).execute().use { response ->
                when {
                    response.isSuccessful -> try {
                        parse(response.body?.string().orEmpty())
                    } catch (e: IllegalArgumentException) {
                        throw ApiException.Malformed()
                    }
                    response.code == 401 ->
                        throw if (sentBearer) ApiException.Unauthorized() else ApiException.InvalidCredentials()
                    response.code == 403 && PasswordChangeRequired.matches(403, response.body?.string()) ->
                        throw ApiException.PasswordChangeRequired()
                    else -> throw ApiException.Http(response.code)
                }
            }
        }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}

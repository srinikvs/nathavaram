package app.nathavaram.voters.net

import app.nathavaram.voters.sync.PushChange
import app.nathavaram.voters.sync.PushRequest
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class HttpApiTest {
    private lateinit var server: MockWebServer
    private var token: String? = "test-token-not-real"

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun api() = HttpApi(OkHttpClient(), { server.url("/").toString().trimEnd('/') }, { token })

    private fun json(body: String, code: Int = 200) =
        MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    private inline fun <reified T : ApiException> expectApiError(block: () -> Unit): T {
        try {
            block()
        } catch (e: ApiException) {
            assertTrue("unexpected ${e::class}", e is T)
            return e as T
        }
        fail("expected ApiException")
        throw AssertionError()
    }

    @Test
    fun `pull sends only a Bearer token and the since and limit parameters`() = runBlocking {
        server.enqueue(json("""{"server_time":"2026-11-11T12:00:00.000000Z","server_revision":3,"since":2,"has_more":false,"changes":[]}"""))

        val page = api().pull(since = 2, limit = 500)

        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/sync/pull?since=2&limit=500", request.path)
        assertEquals("Bearer test-token-not-real", request.getHeader("Authorization"))
        assertNull(request.getHeader("Cookie"))
        assertNull(request.getHeader("X-CSRF-Token"))
        assertEquals(3L, page.serverRevision)
        assertFalse(page.hasMore)
    }

    @Test
    fun `push posts the snapshot and parses per-item results`() = runBlocking {
        server.enqueue(
            json(
                """{"server_time":"2026-11-11T12:00:00.000000Z","server_revision":9,"results":[
                    {"voter_id":"ZZZ0000001","status":"applied","server_revision":9,"server_record":null},
                    {"voter_id":"ZZZ0000002","status":"conflict","message":"newer","server_record":{"voter_id":"ZZZ0000002","first_name":"Existing","revision":4}}
                ]}""",
            ),
        )
        val change = PushChange(
            voterId = "ZZZ0000001", updatedAt = "2026-11-11T11:00:00.000000Z", createdAt = "2026-11-11T10:00:00.000000Z",
            baseRevision = 3, firstName = "Testperson", lastName = "One", houseNo = "12-A", street = "Main Street",
        )

        val response = api().push(PushRequest("android-test", listOf(change)))

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/sync/push", request.path)
        assertEquals("Bearer test-token-not-real", request.getHeader("Authorization"))
        assertNull(request.getHeader("X-CSRF-Token"))
        val body = request.body.readUtf8()
        assertTrue(body.contains("\"device_id\":\"android-test\""))
        assertTrue(body.contains("\"base_revision\":3"))
        assertTrue(body.contains("\"deleted\":false"))
        assertTrue(body.contains("\"voter_id\":\"ZZZ0000001\""))
        assertTrue(body.contains("\"house_no\":\"12-A\""))
        assertTrue("no row id is sent", !body.contains("\"id\":"))
        assertEquals(listOf("applied", "conflict"), response.results.map { it.status })
        assertEquals(listOf("ZZZ0000001", "ZZZ0000002"), response.results.map { it.voterId })
        assertEquals("ZZZ0000002", response.results[1].serverRecord?.voterId)
    }

    @Test
    fun `login sends credentials without an Authorization header and returns the token`() = runBlocking {
        server.enqueue(json("""{"access_token":"issued-token-not-real","token_type":"bearer","expires_in":60,"role":"user","username":"fakeuser","csrf_token":"x"}"""))

        val response = api().login("fakeuser", "fake-password")

        val request = server.takeRequest()
        assertEquals("/api/auth/login", request.path)
        assertNull(request.getHeader("Authorization"))
        assertEquals("issued-token-not-real", response.accessToken)
    }

    @Test
    fun `a rejected login is reported as invalid credentials`() {
        server.enqueue(json("""{"detail":"Invalid username or password"}""", code = 401))

        expectApiError<ApiException.InvalidCredentials> { runBlocking { api().login("fakeuser", "wrong") } }
    }

    @Test
    fun `an expired token on sync is reported as unauthorized`() {
        server.enqueue(json("""{"detail":"expired"}""", code = 401))

        expectApiError<ApiException.Unauthorized> { runBlocking { api().pull(0, 10) } }
    }

    @Test
    fun `sync without a stored token fails before any request is made`() {
        token = null

        expectApiError<ApiException.Unauthorized> { runBlocking { api().pull(0, 10) } }
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `server errors are retryable and client errors are not`() {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(422))

        val unavailable = expectApiError<ApiException.Http> { runBlocking { api().pull(0, 10) } }
        val rejected = expectApiError<ApiException.Http> { runBlocking { api().pull(0, 10) } }

        assertTrue(unavailable.retryable)
        assertFalse(rejected.retryable)
    }

    @Test
    fun `an unreadable body is reported as malformed without echoing it`() {
        server.enqueue(json("not json at all ZZZ0000001"))

        val error = expectApiError<ApiException.Malformed> { runBlocking { api().pull(0, 10) } }

        assertFalse(error.message.orEmpty().contains("ZZZ0000001"))
    }

    @Test
    fun `a missing server address fails without a request`() {
        val api = HttpApi(OkHttpClient(), { null }, { token })

        expectApiError<ApiException.NotConfigured> { runBlocking { api.pull(0, 10) } }
    }
}

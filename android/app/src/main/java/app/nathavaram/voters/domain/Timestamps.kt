package app.nathavaram.voters.domain

import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

/** UTC timestamps in the sync format `YYYY-MM-DDTHH:MM:SS.ffffffZ`. */
object Timestamps {
    private val FORMAT: DateTimeFormatter =
        DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'").withZone(ZoneOffset.UTC)
    private val HAS_ZONE = Regex("(Z|[+-]\\d{2}:\\d{2})$")

    fun format(instant: Instant): String = FORMAT.format(instant)

    fun now(clock: Clock = Clock.systemUTC()): String = format(Instant.now(clock))

    /** A value without a timezone is read as UTC (docs/SYNC.md). Null when unparseable. */
    fun parse(value: String): Instant? = try {
        if (HAS_ZONE.containsMatchIn(value)) {
            OffsetDateTime.parse(value).toInstant()
        } else {
            LocalDateTime.parse(value).toInstant(ZoneOffset.UTC)
        }
    } catch (e: DateTimeParseException) {
        null
    }

    /** True when [a] is strictly later than [b]. Falls back to string order if either is unparseable. */
    fun isNewer(a: String, b: String): Boolean {
        val ia = parse(a)
        val ib = parse(b)
        return if (ia != null && ib != null) ia.isAfter(ib) else a > b
    }
}

/** Warns the operator when the phone clock disagrees with `server_time`. */
object ClockSkew {
    val THRESHOLD: Duration = Duration.ofMinutes(5)

    fun exceeds(serverTime: String, now: Instant = Instant.now(), threshold: Duration = THRESHOLD): Boolean {
        val server = Timestamps.parse(serverTime) ?: return false
        return Duration.between(server, now).abs() > threshold
    }
}

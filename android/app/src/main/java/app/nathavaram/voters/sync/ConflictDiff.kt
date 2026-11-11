package app.nathavaram.voters.sync

data class FieldDiff(val label: String, val local: String?, val server: String?)

/** Lists the editable fields whose values differ between the local edit and the server row. */
object ConflictDiff {
    private val FIELDS: List<Pair<String, (VoterDto) -> String?>> = listOf(
        "2024 Booth" to { it.booth2024 },
        "2024 B.S.No" to { it.bsNo2024 },
        "2026 Ward" to { it.ward2026 },
        "Ward S No" to { it.wardSNo },
        "VoterId" to { it.voterId },
        "FirstName" to { it.firstName },
        "SurName" to { it.surname },
        "RelativeName" to { it.relativeName },
        "HouseNo" to { it.houseNo },
        "Gender" to { it.gender },
        "Age" to { it.age?.toString() },
        "Caste" to { it.caste },
        "Mobile" to { it.mobile },
        "C_City" to { it.cCity },
        "Profession" to { it.profession },
        "Party" to { it.party },
    )

    fun diff(local: VoterDto?, server: VoterDto?): List<FieldDiff> {
        if (local == null || server == null) return emptyList()
        return FIELDS.mapNotNull { (label, get) ->
            val l = get(local)?.ifEmpty { null }
            val s = get(server)?.ifEmpty { null }
            if (l == s) null else FieldDiff(label, l, s)
        }
    }

    fun decode(json: String?): VoterDto? =
        json?.let { runCatching { SyncJson.json.decodeFromString(VoterDto.serializer(), it) }.getOrNull() }
}

package app.nathavaram.voters.sync

data class FieldDiff(val label: String, val local: String?, val server: String?)

/** Lists the editable fields whose values differ between the local edit and the server row. */
object ConflictDiff {
    private val FIELDS: List<Pair<String, (VoterDto) -> String?>> = listOf(
        "Booth" to { it.booth },
        "Booth S.No" to { it.boothSNo },
        "Ward" to { it.ward },
        "Ward S.No" to { it.wardSNo },
        "VoterId" to { it.voterId },
        "FirstName" to { it.firstName },
        "LastName" to { it.lastName },
        "RelativeName" to { it.relativeName },
        "HouseNo" to { it.houseNo },
        "Age" to { it.age?.toString() },
        "Street" to { it.street },
        "Mobile" to { it.mobile },
        "Caste" to { it.caste },
        "Village" to { it.village },
        "Panchayath" to { it.panchayath },
        "Mandal" to { it.mandal },
        "Current City" to { it.currentCity },
        "Local/Non-Local" to { if (it.isLocal) "Local" else "Non-Local" },
        "Party" to { it.party },
        "Constituency" to { it.constituency },
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

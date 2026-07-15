from __future__ import annotations

from dataclasses import dataclass

APPROVED_TOOLS = {
    "arxiv_search_papers": "arxiv",
    "calculator_calculate": "calculator",
    "cli-mcp-server_run_command": "cli-mcp-server",
    "cli-mcp-server_show_security_rules": "cli-mcp-server",
    "clinicaltrialsgov-mcp-server_clinicaltrials_analyze_trends": "clinicaltrialsgov-mcp-server",
    "clinicaltrialsgov-mcp-server_clinicaltrials_get_study": "clinicaltrialsgov-mcp-server",
    "clinicaltrialsgov-mcp-server_clinicaltrials_list_studies": "clinicaltrialsgov-mcp-server",
    "context7_get-library-docs": "context7",
    "context7_resolve-library-id": "context7",
    "ddg-search_fetch_content": "ddg-search",
    "ddg-search_search": "ddg-search",
    "e2b-server_run_code": "e2b-server",
    "exa_web_search_exa": "exa",
    "fetch_fetch": "fetch",
    "filesystem_directory_tree": "filesystem",
    "filesystem_get_file_info": "filesystem",
    "filesystem_list_allowed_directories": "filesystem",
    "filesystem_list_directory": "filesystem",
    "filesystem_list_directory_with_sizes": "filesystem",
    "filesystem_read_file": "filesystem",
    "filesystem_read_media_file": "filesystem",
    "filesystem_read_multiple_files": "filesystem",
    "filesystem_read_text_file": "filesystem",
    "filesystem_search_files": "filesystem",
    "git_git_diff": "git",
    "git_git_diff_staged": "git",
    "git_git_diff_unstaged": "git",
    "git_git_log": "git",
    "git_git_show": "git",
    "git_git_status": "git",
    "github_get_commit": "github",
    "github_get_file_contents": "github",
    "github_get_issue": "github",
    "github_get_issue_comments": "github",
    "github_get_pull_request": "github",
    "github_get_pull_request_comments": "github",
    "github_get_pull_request_files": "github",
    "github_get_pull_request_review_comments": "github",
    "github_get_pull_request_status": "github",
    "github_get_repository": "github",
    "github_get_tag": "github",
    "github_list_branches": "github",
    "github_list_commits": "github",
    "github_list_issues": "github",
    "github_list_pull_requests": "github",
    "github_list_tags": "github",
    "github_search_code": "github",
    "github_search_issues": "github",
    "github_search_repositories": "github",
    "github_search_users": "github",
    "mcp-code-executor_check_installed_packages": "mcp-code-executor",
    "mcp-code-executor_execute_code": "mcp-code-executor",
    "mcp-code-executor_get_environment_config": "mcp-code-executor",
    "mcp-code-executor_read_code_file": "mcp-code-executor",
    "memory_open_nodes": "memory",
    "memory_read_graph": "memory",
    "memory_search_nodes": "memory",
    "met-museum_get-museum-object": "met-museum",
    "met-museum_list-departments": "met-museum",
    "met-museum_search-museum-objects": "met-museum",
    "open-library_get_author_info": "open-library",
    "open-library_get_author_photo": "open-library",
    "open-library_get_authors_by_name": "open-library",
    "open-library_get_book_by_id": "open-library",
    "open-library_get_book_by_title": "open-library",
    "open-library_get_book_cover": "open-library",
    "osm-mcp-server_analyze_commute": "osm-mcp-server",
    "osm-mcp-server_analyze_neighborhood": "osm-mcp-server",
    "osm-mcp-server_explore_area": "osm-mcp-server",
    "osm-mcp-server_find_ev_charging_stations": "osm-mcp-server",
    "osm-mcp-server_find_nearby_places": "osm-mcp-server",
    "osm-mcp-server_find_parking_facilities": "osm-mcp-server",
    "osm-mcp-server_find_schools_nearby": "osm-mcp-server",
    "osm-mcp-server_geocode_address": "osm-mcp-server",
    "osm-mcp-server_get_route_directions": "osm-mcp-server",
    "osm-mcp-server_reverse_geocode": "osm-mcp-server",
    "osm-mcp-server_search_category": "osm-mcp-server",
    "osm-mcp-server_suggest_meeting_point": "osm-mcp-server",
    "pubmed_get_pubmed_article_metadata": "pubmed",
    "pubmed_search_pubmed_advanced": "pubmed",
    "pubmed_search_pubmed_key_words": "pubmed",
    "weather_find_weather_stations": "weather",
    "weather_get_current_weather": "weather",
    "weather_get_hourly_forecast": "weather",
    "weather_get_local_time": "weather",
    "weather_get_weather_alerts": "weather",
    "weather_get_weather_forecast": "weather",
    "whois_whois_as": "whois",
    "whois_whois_domain": "whois",
    "whois_whois_ip": "whois",
    "whois_whois_tld": "whois",
    "wikipedia_extract_key_facts": "wikipedia",
    "wikipedia_get_article": "wikipedia",
    "wikipedia_get_links": "wikipedia",
    "wikipedia_get_related_topics": "wikipedia",
    "wikipedia_get_sections": "wikipedia",
    "wikipedia_get_summary": "wikipedia",
    "wikipedia_search_wikipedia": "wikipedia",
    "wikipedia_summarize_article_for_query": "wikipedia",
    "wikipedia_summarize_article_section": "wikipedia",
}
APPROVED_EXTERNAL_SERVERS = frozenset({"e2b-server", "exa", "github"})
APPROVED_SERVERS = frozenset(APPROVED_TOOLS.values())
PUBLIC_SERVERS = APPROVED_SERVERS - APPROVED_EXTERNAL_SERVERS


@dataclass(frozen=True)
class McpAtlasProfile:
    tools: dict[str, str]

    def permits(self, tool: str, server: str) -> bool:
        return self.tools.get(tool) == server

    def validate(self, tool_servers: dict[str, str]) -> None:
        denied = sorted(
            f"{server}:{tool}"
            for tool, server in tool_servers.items()
            if not self.permits(tool, server)
        )
        if denied:
            raise ValueError(f"MCP-Atlas executable profile denies: {', '.join(denied)}")


EXECUTABLE_PROFILE = McpAtlasProfile(APPROVED_TOOLS)

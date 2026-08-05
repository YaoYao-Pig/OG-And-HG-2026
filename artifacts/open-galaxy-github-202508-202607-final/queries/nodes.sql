WITH
    global_repo_months AS
    (
        SELECT
            repo_id,
            toYYYYMM(created_at) AS yyyymm,

            argMax(repo_name, tuple(toFloat64(openrank), repo_name)) AS repo_name,
            argMax(org_login, tuple(toFloat64(openrank), org_login)) AS org_login,
            max(toFloat64(openrank)) AS month_openrank
        FROM global_openrank
        WHERE platform = 'GitHub'
          AND type = 'Repo'
          AND toYYYYMM(created_at) BETWEEN 202508 AND 202607
          AND repo_id != 0
        GROUP BY repo_id, yyyymm
    ),
    repo_scores AS
    (
        SELECT
            repo_id,

            argMax(repo_name, tuple(yyyymm, month_openrank, repo_name)) AS repo_name,
            argMax(org_login, tuple(yyyymm, month_openrank, org_login)) AS org_login,
            avg(month_openrank) AS openrank_avg,
            max(month_openrank) AS openrank_max,
            uniqExact(yyyymm) AS months_present,
            sum(month_openrank) AS openrank_sum
        FROM global_repo_months
        GROUP BY repo_id
    ),
    latest_repo_info AS
    (
        SELECT
            id,
            argMax(isFork, tuple(updated_at, isFork)) AS is_fork
        FROM repo_info
        WHERE platform = 'GitHub'
        GROUP BY id
    ),
    top_repos AS
    (
        SELECT scores.*
        FROM repo_scores AS scores
        LEFT JOIN latest_repo_info AS info ON info.id = scores.repo_id
        WHERE info.id = 0 OR info.is_fork = 0
        ORDER BY scores.openrank_sum DESC, scores.repo_id
        LIMIT 27000
    ),
    normalized_repo_actor_months AS
    (
        SELECT
            repo_id,
            actor_id,
            yyyymm,
            argMax(repo_name, tuple(toFloat64(openrank), repo_name)) AS repo_name,
            max(toFloat64(openrank)) AS month_contribution
        FROM normalized_community_openrank
        WHERE platform = 'GitHub'
          AND yyyymm BETWEEN 202508 AND 202607
          AND actor_id != 0
          AND repo_id IN (SELECT repo_id FROM top_repos)
        GROUP BY repo_id, actor_id, yyyymm
    ),
    contribution_stats AS
    (
        SELECT
            repo_id,
            argMax(repo_name, tuple(yyyymm, month_contribution, repo_name)) AS repo_name,
            uniqExact(actor_id) AS contributors,
            sum(month_contribution) AS normalized_contribution
        FROM normalized_repo_actor_months
        GROUP BY repo_id
    ),
    latest_node_info AS
    (
        SELECT
            id,
            argMax(status, updated_at) AS status,
            argMax(isFork, tuple(updated_at, isFork)) AS is_fork,
            argMax(primary_language, updated_at) AS primary_language,
            argMax(topics, updated_at) AS topics,
            argMax(description, updated_at) AS description
        FROM repo_info
        WHERE platform = 'GitHub'
          AND id IN (SELECT repo_id FROM top_repos)
        GROUP BY id
    )
SELECT
    concat('GitHub', ':', toString(top.repo_id)) AS id,
    if(notEmpty(top.repo_name), top.repo_name, stats.repo_name) AS name,
    'GitHub' AS platform,
    top.repo_id AS repo_id,
    round(top.openrank_sum, 6) AS openrank_sum,
    round(top.openrank_avg, 6) AS openrank_avg,
    round(top.openrank_max, 6) AS openrank_max,
    top.months_present AS months_present,
    stats.contributors AS contributors,
    round(stats.normalized_contribution, 6) AS normalized_contribution,
    if(notEmpty(info.primary_language), info.primary_language, 'Unknown') AS primary_language,
    arrayStringConcat(arraySlice(info.topics, 1, 12), '|') AS topics,
    replaceRegexpAll(substringUTF8(info.description, 1, 300), '[\r\n\t]+', ' ') AS description,
    if(info.id = 0, 'missing', toString(info.status)) AS metadata_status,
    if('GitHub' = 'GitHub', concat('https://github.com/', name), '') AS url
FROM top_repos AS top
LEFT JOIN contribution_stats AS stats ON stats.repo_id = top.repo_id
LEFT JOIN latest_node_info AS info ON info.id = top.repo_id
ORDER BY top.openrank_sum DESC, top.repo_id
FORMAT CSVWithNames

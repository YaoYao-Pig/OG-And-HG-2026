WITH
    global_repo_months AS
    (
        SELECT
            repo_id,
            toYYYYMM(created_at) AS yyyymm,

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
    )
SELECT
    (SELECT count()
     FROM global_openrank
     WHERE platform = 'GitHub'
       AND type = 'Repo'
       AND toYYYYMM(created_at) BETWEEN 202508 AND 202607
       AND repo_id IN (SELECT repo_id FROM top_repos)) AS global_rows,
    (SELECT uniqExact(tuple(repo_id, toYYYYMM(created_at)))
     FROM global_openrank
     WHERE platform = 'GitHub'
       AND type = 'Repo'
       AND toYYYYMM(created_at) BETWEEN 202508 AND 202607
       AND repo_id IN (SELECT repo_id FROM top_repos)) AS global_unique_keys,
    (SELECT count()
     FROM
     (
         SELECT repo_id, toYYYYMM(created_at) AS yyyymm
         FROM global_openrank
         WHERE platform = 'GitHub'
           AND type = 'Repo'
           AND toYYYYMM(created_at) BETWEEN 202508 AND 202607
           AND repo_id IN (SELECT repo_id FROM top_repos)
         GROUP BY repo_id, yyyymm
         HAVING uniqExact(toFloat64(openrank)) > 1
     )) AS global_conflicting_duplicate_keys,
    (SELECT count()
     FROM normalized_community_openrank
     WHERE platform = 'GitHub'
       AND yyyymm BETWEEN 202508 AND 202607
       AND repo_id IN (SELECT repo_id FROM top_repos)) AS normalized_rows,
    (SELECT uniqExact(tuple(repo_id, actor_id, yyyymm))
     FROM normalized_community_openrank
     WHERE platform = 'GitHub'
       AND yyyymm BETWEEN 202508 AND 202607
       AND repo_id IN (SELECT repo_id FROM top_repos)) AS normalized_unique_keys,
    (SELECT count()
     FROM
     (
         SELECT repo_id, actor_id, yyyymm
         FROM normalized_community_openrank
         WHERE platform = 'GitHub'
           AND yyyymm BETWEEN 202508 AND 202607
           AND repo_id IN (SELECT repo_id FROM top_repos)
         GROUP BY repo_id, actor_id, yyyymm
         HAVING uniqExact(toFloat64(openrank)) > 1
     )) AS normalized_conflicting_duplicate_keys
FORMAT TabSeparatedRaw

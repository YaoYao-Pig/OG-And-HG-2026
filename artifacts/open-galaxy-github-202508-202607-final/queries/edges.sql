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
    ),
    actor_annual_degrees AS
    (
        SELECT
            actor_id,
            uniqExact(repo_id) AS annual_repo_degree
        FROM normalized_community_openrank
        WHERE platform = 'GitHub'
          AND yyyymm BETWEEN 202508 AND 202607
          AND actor_id != 0
        GROUP BY actor_id
        HAVING annual_repo_degree BETWEEN 2 AND 30
    ),
    actor_repo_months AS
    (
        SELECT
            actor_id,
            repo_id,
            yyyymm,
            max(toFloat64(openrank)) AS month_contribution
        FROM normalized_community_openrank
        WHERE platform = 'GitHub'
          AND yyyymm BETWEEN 202508 AND 202607
          AND actor_id IN (SELECT actor_id FROM actor_annual_degrees)
          AND repo_id IN (SELECT repo_id FROM top_repos)
        GROUP BY actor_id, repo_id, yyyymm
    ),
    actor_repo AS
    (
        SELECT
            actor_id,
            repo_id,
            sum(month_contribution) AS contribution
        FROM actor_repo_months
        GROUP BY actor_id, repo_id
        HAVING contribution > 0
    ),
    selected_actor_bundles AS
    (
        SELECT
            actor_id,
            arraySort(groupArray(30)((repo_id, contribution))) AS repos,
            length(repos) AS selected_repo_degree
        FROM actor_repo
        GROUP BY actor_id
        HAVING selected_repo_degree >= 2
    ),
    actor_bundles AS
    (
        SELECT
            bundles.actor_id,
            bundles.repos,
            degrees.annual_repo_degree
        FROM selected_actor_bundles AS bundles
        INNER JOIN actor_annual_degrees AS degrees ON degrees.actor_id = bundles.actor_id
    ),
    pair_rows AS
    (
        SELECT
            tupleElement(repos[i], 1) AS source_repo_id,
            tupleElement(repos[j], 1) AS target_repo_id,
            tupleElement(repos[i], 2) AS source_contribution,
            tupleElement(repos[j], 2) AS target_contribution,
            2.0 / (toFloat64(annual_repo_degree) * (annual_repo_degree - 1)) AS actor_weight
        FROM actor_bundles
        ARRAY JOIN arrayEnumerate(repos) AS i
        ARRAY JOIN arrayEnumerate(repos) AS j
        WHERE i < j
    ),
    aggregated_edges AS
    (
        SELECT
            source_repo_id,
            target_repo_id,
            round(sum(actor_weight), 8) AS weight,
            count() AS shared_contributors,
            round(
                sum(actor_weight * sqrt(source_contribution * target_contribution)),
                8
            ) AS collaboration_strength
        FROM pair_rows
        GROUP BY source_repo_id, target_repo_id
        HAVING shared_contributors >= 2
    ),
    incident_edges AS
    (
        SELECT
            tupleElement(endpoint, 1) AS node_repo_id,
            tupleElement(endpoint, 2) AS neighbor_repo_id,
            weight,
            shared_contributors,
            collaboration_strength
        FROM aggregated_edges
        ARRAY JOIN [
            (source_repo_id, target_repo_id),
            (target_repo_id, source_repo_id)
        ] AS endpoint
    ),
    ranked_incident_edges AS
    (
        SELECT
            *,
            row_number() OVER
            (
                PARTITION BY node_repo_id
                ORDER BY weight DESC, shared_contributors DESC, neighbor_repo_id
            ) AS edge_rank
        FROM incident_edges
    ),
    pruned_edges AS
    (
        SELECT
            least(node_repo_id, neighbor_repo_id) AS source_repo_id,
            greatest(node_repo_id, neighbor_repo_id) AS target_repo_id,
            max(weight) AS weight,
            max(shared_contributors) AS shared_contributors,
            max(collaboration_strength) AS collaboration_strength
        FROM ranked_incident_edges
        WHERE edge_rank <= 18
        GROUP BY source_repo_id, target_repo_id
    )
SELECT
    concat('GitHub', ':', toString(source_repo_id)) AS source,
    concat('GitHub', ':', toString(target_repo_id)) AS target,
    weight,
    shared_contributors,
    collaboration_strength
FROM pruned_edges
ORDER BY weight DESC, shared_contributors DESC, source_repo_id, target_repo_id
FORMAT CSVWithNames

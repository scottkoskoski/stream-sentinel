"""
Service Dependency Manager for Integration Testing

Manages service dependencies and resolves startup ordering using topological sort.
Ensures services start in the correct order based on their dependencies.

Key Features:
- Topological sorting for dependency resolution
- Cycle detection in dependency graphs
- Parallel startup group identification
- Comprehensive error handling for invalid dependencies
"""

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


class CircularDependencyError(Exception):
    """Raised when circular dependencies are detected."""

    def __init__(self, cycle: List[str]):
        self.cycle = cycle
        cycle_str = " -> ".join(cycle + [cycle[0]])
        super().__init__(f"Circular dependency detected: {cycle_str}")


class InvalidDependencyError(Exception):
    """Raised when an invalid dependency is specified."""

    def __init__(self, service: str, invalid_dependency: str):
        self.service = service
        self.invalid_dependency = invalid_dependency
        super().__init__(f"Service '{service}' depends on unknown service '{invalid_dependency}'")


@dataclass
class DependencyNode:
    """Represents a service in the dependency graph."""

    name: str
    dependencies: Set[str]
    dependents: Set[str]
    startup_priority: int = 0  # Lower numbers start first


class ServiceDependencyManager:
    """
    Manages service dependencies and resolves startup ordering.

    Uses topological sorting to determine the correct order for starting
    services based on their dependencies.
    """

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.ServiceDependencyManager")
        self._dependency_graph: Dict[str, DependencyNode] = {}

    def resolve_startup_order(self, dependencies: Dict[str, List[str]]) -> List[str]:
        """
        Resolve service startup order using topological sort.

        Args:
            dependencies: Dict mapping service names to their dependencies

        Returns:
            List of service names in startup order

        Raises:
            CircularDependencyError: If circular dependencies are detected
            InvalidDependencyError: If invalid dependencies are specified
        """
        self.logger.debug(f"Resolving startup order for {len(dependencies)} services")

        # Build dependency graph
        self._build_dependency_graph(dependencies)

        # Validate dependencies
        self._validate_dependencies()

        # Detect cycles
        self._detect_cycles()

        # Perform topological sort
        startup_order = self._topological_sort()

        self.logger.info(f"Resolved startup order: {' -> '.join(startup_order)}")
        return startup_order

    def _build_dependency_graph(self, dependencies: Dict[str, List[str]]):
        """Build internal dependency graph representation."""
        self._dependency_graph.clear()

        # Initialize all services as nodes
        for service_name in dependencies.keys():
            self._dependency_graph[service_name] = DependencyNode(
                name=service_name,
                dependencies=set(dependencies.get(service_name, [])),
                dependents=set(),
            )

        # Build reverse dependency relationships
        for service_name, deps in dependencies.items():
            for dependency in deps:
                if dependency in self._dependency_graph:
                    self._dependency_graph[dependency].dependents.add(service_name)

        # Assign startup priorities based on dependency depth
        self._assign_startup_priorities()

    def _assign_startup_priorities(self):
        """Assign startup priorities based on dependency depth."""
        # Services with no dependencies get priority 0
        # Each level of dependency increases priority by 1

        visited = set()

        def calculate_depth(service_name: str) -> int:
            if service_name in visited:
                return self._dependency_graph[service_name].startup_priority

            visited.add(service_name)
            node = self._dependency_graph[service_name]

            if not node.dependencies:
                node.startup_priority = 0
            else:
                max_dep_priority = max(calculate_depth(dep) for dep in node.dependencies)
                node.startup_priority = max_dep_priority + 1

            return node.startup_priority

        for service_name in self._dependency_graph:
            calculate_depth(service_name)

        # Log priority assignments
        for service_name, node in self._dependency_graph.items():
            self.logger.debug(f"Service {service_name} priority: {node.startup_priority}")

    def _validate_dependencies(self):
        """Validate that all dependencies reference existing services."""
        for service_name, node in self._dependency_graph.items():
            for dependency in node.dependencies:
                if dependency not in self._dependency_graph:
                    raise InvalidDependencyError(service_name, dependency)

    def _detect_cycles(self):
        """Detect circular dependencies using DFS."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {service: WHITE for service in self._dependency_graph}
        parent = {service: None for service in self._dependency_graph}

        def dfs_visit(service: str, path: List[str]) -> Optional[List[str]]:
            """DFS visit with cycle detection."""
            color[service] = GRAY
            path.append(service)

            node = self._dependency_graph[service]
            for dependency in node.dependencies:
                if color[dependency] == GRAY:
                    # Found a back edge - cycle detected
                    cycle_start = path.index(dependency)
                    cycle = path[cycle_start:] + [dependency]
                    return cycle
                elif color[dependency] == WHITE:
                    cycle = dfs_visit(dependency, path.copy())
                    if cycle:
                        return cycle

            color[service] = BLACK
            return None

        for service in self._dependency_graph:
            if color[service] == WHITE:
                cycle = dfs_visit(service, [])
                if cycle:
                    raise CircularDependencyError(cycle)

    def _topological_sort(self) -> List[str]:
        """Perform topological sort using Kahn's algorithm."""
        # Calculate in-degrees
        in_degree = {service: 0 for service in self._dependency_graph}
        for service_name, node in self._dependency_graph.items():
            for dependency in node.dependencies:
                in_degree[service_name] += 1

        # Initialize queue with services having no dependencies
        queue = deque([service for service, degree in in_degree.items() if degree == 0])

        # Sort initial queue by priority to ensure deterministic ordering
        queue = deque(sorted(queue, key=lambda s: self._dependency_graph[s].startup_priority))

        result = []

        while queue:
            # Process services in priority order
            current_service = queue.popleft()
            result.append(current_service)

            # Reduce in-degree for dependent services
            node = self._dependency_graph[current_service]
            newly_available = []

            for dependent in node.dependents:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    newly_available.append(dependent)

            # Sort newly available services by priority
            newly_available.sort(key=lambda s: self._dependency_graph[s].startup_priority)
            queue.extend(newly_available)

        # Verify all services were included
        if len(result) != len(self._dependency_graph):
            missing = set(self._dependency_graph.keys()) - set(result)
            raise CircularDependencyError(list(missing))

        return result

    def get_parallel_startup_groups(self, dependencies: Dict[str, List[str]]) -> List[List[str]]:
        """
        Get groups of services that can be started in parallel.

        Services in the same group have no dependencies on each other
        and can be started concurrently.

        Returns:
            List of lists, where each inner list contains services
            that can be started in parallel
        """
        startup_order = self.resolve_startup_order(dependencies)

        # Group services by their startup priority
        priority_groups = defaultdict(list)
        for service_name in startup_order:
            priority = self._dependency_graph[service_name].startup_priority
            priority_groups[priority].append(service_name)

        # Return groups in order of priority
        groups = []
        for priority in sorted(priority_groups.keys()):
            groups.append(priority_groups[priority])

        self.logger.info(f"Identified {len(groups)} parallel startup groups")
        for i, group in enumerate(groups):
            self.logger.debug(f"Group {i}: {', '.join(group)}")

        return groups

    def get_shutdown_order(self, dependencies: Dict[str, List[str]]) -> List[str]:
        """
        Get service shutdown order (reverse of startup order).

        Services should be shut down in reverse dependency order
        to ensure clean shutdown.
        """
        startup_order = self.resolve_startup_order(dependencies)
        shutdown_order = list(reversed(startup_order))

        self.logger.debug(f"Shutdown order: {' -> '.join(shutdown_order)}")
        return shutdown_order

    def get_service_dependencies(self, service_name: str) -> Dict[str, List[str]]:
        """
        Get comprehensive dependency information for a service.

        Returns:
            Dict containing direct dependencies, transitive dependencies,
            and dependent services
        """
        if service_name not in self._dependency_graph:
            raise ValueError(f"Unknown service: {service_name}")

        node = self._dependency_graph[service_name]

        # Get transitive dependencies using DFS
        transitive_deps = set()
        visited = set()

        def collect_dependencies(svc_name: str):
            if svc_name in visited:
                return
            visited.add(svc_name)

            svc_node = self._dependency_graph[svc_name]
            for dep in svc_node.dependencies:
                transitive_deps.add(dep)
                collect_dependencies(dep)

        collect_dependencies(service_name)

        # Get transitive dependents
        transitive_dependents = set()
        visited = set()

        def collect_dependents(svc_name: str):
            if svc_name in visited:
                return
            visited.add(svc_name)

            svc_node = self._dependency_graph[svc_name]
            for dependent in svc_node.dependents:
                transitive_dependents.add(dependent)
                collect_dependents(dependent)

        collect_dependents(service_name)

        return {
            "direct_dependencies": list(node.dependencies),
            "transitive_dependencies": list(transitive_deps),
            "direct_dependents": list(node.dependents),
            "transitive_dependents": list(transitive_dependents),
            "startup_priority": node.startup_priority,
        }

    def validate_dependency_graph(self, dependencies: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """
        Validate dependency graph and return validation results.

        Returns:
            Dict containing validation results and any issues found
        """
        validation_results = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "statistics": {},
        }

        try:
            # Attempt to build and validate graph
            self._build_dependency_graph(dependencies)
            self._validate_dependencies()
            self._detect_cycles()

            # Calculate statistics
            total_services = len(dependencies)
            services_with_deps = sum(1 for deps in dependencies.values() if deps)
            max_depth = max(node.startup_priority for node in self._dependency_graph.values())

            validation_results["statistics"] = {
                "total_services": total_services,
                "services_with_dependencies": services_with_deps,
                "services_without_dependencies": total_services - services_with_deps,
                "maximum_dependency_depth": max_depth,
                "average_dependencies_per_service": (
                    sum(len(deps) for deps in dependencies.values()) / total_services if total_services > 0 else 0
                ),
            }

            # Check for potential issues
            self._check_for_warnings(dependencies, validation_results)

        except CircularDependencyError as e:
            validation_results["valid"] = False
            validation_results["errors"].append(f"Circular dependency: {e}")

        except InvalidDependencyError as e:
            validation_results["valid"] = False
            validation_results["errors"].append(f"Invalid dependency: {e}")

        except Exception as e:
            validation_results["valid"] = False
            validation_results["errors"].append(f"Validation error: {e}")

        return validation_results

    def _check_for_warnings(self, dependencies: Dict[str, List[str]], validation_results: Dict):
        """Check for potential issues and add warnings."""
        # Check for services with many dependencies
        for service, deps in dependencies.items():
            if len(deps) > 5:
                validation_results["warnings"].append(
                    f"Service '{service}' has {len(deps)} dependencies, consider refactoring"
                )

        # Check for very deep dependency chains
        if self._dependency_graph:
            max_depth = max(node.startup_priority for node in self._dependency_graph.values())
            if max_depth > 5:
                validation_results["warnings"].append(
                    f"Deep dependency chain detected (depth: {max_depth}), " "may cause slow startup times"
                )

        # Check for services with no dependents (potential unused services)
        for service_name, node in self._dependency_graph.items():
            if not node.dependents and node.dependencies:
                validation_results["warnings"].append(
                    f"Service '{service_name}' has dependencies but no dependents, " "verify it's needed"
                )

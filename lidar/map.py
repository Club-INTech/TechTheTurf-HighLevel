import numpy as np

from Vector import *
from Lidar import *


def sub(lhs, rhs):
    return lhs[0] - rhs[0], lhs[1] - rhs[1]


class Map:
    # adjacency_list: dict
    board: np.ndarray
    visited: np.ndarray
    h: np.ndarray
    width: int
    height: int
    path: list
    lidar_collider_list: list
    tmp_angle_dist: list

    def __init__(self, board_width, board_height):
        self.width = board_width
        self.height = board_height
        self.board = np.zeros((board_width, board_height))
        self.visited = np.zeros((board_width, board_height))
        self.h = np.zeros((board_width, board_height))
        self.h[:, :] = float("NaN")
        self.path = []
        self.lidar_collider_list = []
        self.tmp_angle_dist = []

    def get_neighbours(self, v: Vec2):
        neighbours = []
        for j in range(-1, 2):
            for i in range(-1, 2):
                coord = Vec2(int(v.x) + i, int(v.y) + j)
                if 0 <= coord.x < self.width and 0 <= coord.y < self.height:
                    if self.board[int(coord.y)][int(coord.x)] == 0:
                        neighbours.append((coord, Vec2(int(coord.x), int(coord.y)).norm()))
        return neighbours

    def update_collider_with_lidar(self, position: Vec2, orientation: 0.0, laser: HokuyoLX, res: int):
        timestamp, scan = laser.get_dist()
        for pos in self.lidar_collider_list:
            self.update_collider(pos, 0)

        for angle_index in range(laser.amin, laser.amax):
            dist = scan[angle_index]
            angle = float(angle_index) / (laser.amax - laser.amin) * 280
            self.tmp_angle_dist.append((angle, dist))
            pos = Vec2(1.0, 0.0).rotate(angle + orientation).scale(dist).add(position)
            if 0 <= pos.x < self.width and 0 <= pos.y < self.height:
                self.update_collider(position, 1)
                self.lidar_collider_list.append(pos)

    def update_collider(self, position: Vec2, value):
        # if self.board[position[1]][position[0]] != value:
        self.board[int(position.y)][int(position.x)] = value

    def get_h(self, n, stop_node):
        return 1
        # dist = sub(stop_node, n)
        # mn = float(abs(min(dist, key=abs)))
        # mx = float(abs(max(dist, key=abs)))
        # return mn * 2 ** 0.5 + (mx - mn)

    def a_star(self, start_node, stop_node):
        # open_list is a list of nodes which have been visited, but who's neighbors
        # haven't all been inspected, starts off with the start node
        # closed_list is a list of nodes which have been visited
        # and who's neighbors have been inspected
        open_list = {start_node}
        closed_list = set([])
        # g contains current distances from start_node to all other nodes.
        # the default value (if it's not found in the map) is +infinity
        g = {start_node: 0}

        # parents contains an adjacency map of all nodes
        parents = {start_node: start_node}

        while len(open_list) > 0:
            n = None

            # find a node with the lowest value of f() - evaluation function
            for v in open_list:
                if n is None or g[v] + self.get_h(v, stop_node) < g[n] + self.get_h(n, stop_node):
                    n = v

            if n is None:
                print("Path does not exist")
                return None

            # if the current node is the stop_node
            # then we begin reconstructing the path from it to the start_node
            if n == stop_node:
                reconst_path = []

                while parents[n] != n:
                    reconst_path.append(n)
                    n = parents[n]

                reconst_path.append(start_node)
                reconst_path.reverse()

                # print("Path found: {}".format(reconst_path))
                return reconst_path

            # For all neighbours of the current node do
            for (m, weight) in self.get_neighbours(n):
                # if the current node isn't in both open_list and closed_list
                # add it to open_list and note n as it's parent
                if m not in open_list and m not in closed_list:
                    open_list.add(m)

                    self.visited[int(m.y)][int(m.x)] = 1
                    parents[m] = n
                    g[m] = g[n] + weight

                    # otherwise, check if it's quicker to first visit n, then m
                    # and if it is, update parent data and g data
                    # and if the node was in the closed_list, move it to open_list
                else:
                    if g[m] > g[n] + weight:
                        g[m] = g[n] + weight
                        parents[m] = n

                        if m in closed_list:
                            closed_list.remove(m)
                            open_list.add(m)
                            self.visited[m[1]][m[0]] = 1

                # remove n from the open_list, and add it to closed_list
                # because all of his neighbors were inspected
            open_list.remove(n)
            closed_list.add(n)

        print('Path does not exist!')
        return None

    def collides_on_line(self, pos_from: Vec2, pos_to: Vec2):
        x0 = min(pos_from.x, pos_to.x)
        x1 = max(pos_from.x, pos_to.x)
        y0 = min(pos_from.y, pos_to.y)
        y1 = max(pos_from.y, pos_to.y)

        dx = x1 - x0
        dy = y1 - y0
        if dx > dy:
            for x in range(int(x0), int(x1 + 1)):
                y = int(y0 + dy * (x - x1) / dx)
                if self.board[y][x] == 1:
                    return True
            for y in range(int(y0), int(y1 + 1)):
                x = int(x0 + dx * (y - y1) / dy)
                if self.board[y][x] == 1:
                    return True
        return False

    def vectorize_path(self, pos: Vec2):
        for i in range(len(self.path) - 1, 0, -1):
            if not self.collides_on_line(pos, self.path[i]):
                for j in range(i - 1):
                    self.path.pop(1)
                return

    def get_path(self, start: Vec2, finish: Vec2):
        self.path = self.a_star(start, finish)
        self.simplify_path()
        self.vectorize_path(start)

    def simplify_path(self):
        i = 1
        while i < len(self.path) - 1:
            delta0 = self.path[i].sub(self.path[i - 1]).normalize()
            delta1 = self.path[i + 1].sub(self.path[i]).normalize()
            # delta0 = normalize(sub(self.path[i], self.path[i - 1]))
            # delta1 = normalize(sub(self.path[i + 1], self.path[i]))

            # if norm(sub(delta0, delta1)) < 0.1:
            if delta0.sub(delta1).norm() < 0.1:
                del self.path[i]
            else:
                i += 1

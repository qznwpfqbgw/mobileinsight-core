#! /usr/bin/env python
"""
handoff_loop_analyzer.py

An analyzer for handoff loop detection and resolution

Author: Yuanjie Li
"""
import xml.etree.ElementTree as ET
from analyzer import *
from rrc_analyzer import RrcAnalyzer

class HandoffLoopAnalyzer(Analyzer):

	def __init__(self):

		Analyzer.__init__(self)

		self.__rrc_analyzer = RrcAnalyzer()

		self.include_analyzer(self.__rrc_analyzer,[self.__loop_detection])

	def __loop_detection(self,msg):
		"""
			Detection persistent loops, configure device to avoid it
		"""

		if msg.type_id!="RrcAnalyzer":
			return

		#Get cell list and configurations
		cell_list = self.__rrc_analyzer.get_cell_list()
		# mark if a cell has been visited
		cell_visited = {x:False for x in cell_list} 
		

		#test
		# print cell_list
		for cell in cell_list:
			# self.__rrc_analyzer.get_cell_config(cell).dump()
			self.logger.info(self.__rrc_analyzer.get_cell_config(cell).dump())

		# each cell's configuration
		cell_config = {}
		for cell in cell_list:
			cell_config[cell]=self.__rrc_analyzer.get_cell_config(cell)
		
		if cell_list:

			# We implement the loop detection algorithm in Proposition 3,
			# because preferences are observed to be inconsistent

			while False in cell_visited.itervalues():	
				# some cells have not been explored yet	

				# In each round, we report loops with *unvisited_cell* involved			
				unvisited_cell=None
				#find an unvisited cell
				for cell in cell_list:
					if not cell_visited[cell]:
						unvisited_cell=cell
						break

				neighbor_cells = self.__rrc_analyzer.get_cell_neighbor(unvisited_cell)
				#For each cell: 0 for unvisited, 1 for idle-only visited, 2 for idle-active visited
				neighbor_visited = {x: 0 for x in neighbor_cells}

				#visited cells are ruled out
				for item in neighbor_cells:
					# if cell_visited.has_key(item) and cell_visited[item]:
					if item in cell_visited and cell_visited[item]:
						neighbor_visited[item]=2
				
				#stacks
				dfs_stack=[unvisited_cell]	
				neighbor_stack=[neighbor_visited]
				#dcell_clear_stack[i]=false if ci-1->ci is "high-pref" handoff, true otherwise
				cell_clear_stack=[True]
				val_stack=[0]

				while dfs_stack:
					self.logger.debug("dfs_stack:"+str(dfs_stack))
					src_cell = dfs_stack.pop()
					src_neighbor = neighbor_stack.pop()
					src_clear = cell_clear_stack.pop()
					src_val = val_stack.pop()
					dst_cell = None
					dst_clear = None
					dst_val = None

					#Find a next cell to handoff
					for cell in src_neighbor:
						if src_neighbor[cell]<2 \
						and (not dfs_stack or cell not in dfs_stack[1:]):
							dst_cell = cell
							break

					if dst_cell==None:
						#src_cell's all neighbors have been visited
						continue

					src_neighbor[dst_cell]+=1
					self.logger.debug("dst_cell:"+str(dst_cell)\
						+" state:"+str(src_neighbor[dst_cell]))

					src_freq=cell_config[src_cell].status.freq
					dst_freq=cell_config[dst_cell].status.freq
					dst_config=None
					if src_neighbor[dst_cell]==1:
						#idle-state handoff
						dst_config=cell_config[src_cell].get_cell_reselection_config(cell_config[dst_cell].status)
					else:
						#active-state handoff
						dst_config=cell_config[src_cell].get_meas_config(cell_config[dst_cell].status)

					if dst_config==None:
						dfs_stack.append(src_cell)
						neighbor_stack.append(src_neighbor)
						cell_clear_stack.append(src_clear)
						val_stack.append(src_val)
						continue

					src_pref=cell_config[src_cell].sib.serv_config.priority
					dst_pref=dst_config.priority

					if src_pref==None or dst_pref==None:	#happens in 3G
						#25.331: without pref, treat as equal pref
						src_pref = dst_pref = None

					
					dst_neighbor_cells=self.__rrc_analyzer.get_cell_neighbor(dst_cell)
					dst_neighbor={x: 0 for x in dst_neighbor_cells}
					for item in dst_neighbor:
						# if cell_visited.has_key(item) and cell_visited[item]:
						if item in cell_visited and cell_visited[item]:
							dst_neighbor[item]=2

					dfs_stack.append(src_cell)
					neighbor_stack.append(src_neighbor)
					cell_clear_stack.append(src_clear)
					val_stack.append(src_val)

					if src_freq==dst_freq or src_pref==dst_pref:
						if dst_config.offset!=None:
							dst_clear = True
							dst_val = src_val+dst_config.offset
						else: #trace not ready
							continue
					elif src_pref<dst_pref:
						dst_clear = False
						dst_val = dst_config.threshx_high
					elif src_pref>dst_pref:
						if src_val>dst_config.threshserv_low:
							continue
						dst_clear = True
						dst_val = src_val+dst_config.threshx_low-dst_config.threshserv_low
						
					if dst_cell == dfs_stack[0]:
						#Loop may occur
						#Special check: if dfs_stack[0] performs low-pref handoff
						dst_dst_cell = dfs_stack[1]
						if neighbor_stack[0][dst_dst_cell]==1:
							#idle-state handoff
							dst_dst_config=cell_config[dst_cell].get_cell_reselection_config(cell_config[dst_dst_cell].status)
						else:
							#active-state handoff
							dst_dst_config=cell_config[dst_cell].get_meas_config(cell_config[dst_dst_cell].status)

						dst_serv_pref = cell_config[dst_cell].sib.serv_config.priority
						dst_dst_pref = dst_dst_config.priority

						if dst_serv_pref==None or dst_dst_pref==None:	#happens in 3G
							#25.331: without pref, treat as equal pref
							dst_serv_pref = dst_dst_pref = None

						if dst_serv_pref>dst_dst_pref \
						and dst_val>dst_dst_config.threshserv_low:
							continue

						if False in cell_clear_stack or dst_val<0:
							loop_report="\033[91m\033[1mPersistent loop: \033[0m\033[0m"
							loop_report+=str(dfs_stack[0])
							prev_item=dfs_stack[0]
							for item in range(1,len(dfs_stack)):
								cell=dfs_stack[item]
								if neighbor_stack[item-1][cell]==1:
									loop_report+="(idle)->"+str(cell)
								elif neighbor_stack[item-1][cell]==2:
									loop_report+="(active)->"+str(cell)

							if src_neighbor[dst_cell]==1:
								loop_report+="(idle)->"+str(dst_cell)
							elif src_neighbor[dst_cell]==2:
								loop_report+="(active)->"+str(dst_cell)

							self.logger.warning(loop_report)
							continue

					dfs_stack.append(dst_cell)
					neighbor_stack.append(dst_neighbor)
					cell_clear_stack.append(dst_clear)
					val_stack.append(dst_val)



				cell_visited[unvisited_cell]=True




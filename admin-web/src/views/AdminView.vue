<script setup>
import SvgIcon from '../components/SvgIcon.vue'
import DataTable from '../components/admin/DataTable.vue'
</script>

<template>
  <div>
    <div class="panel-title" style="margin-bottom:12px"><SvgIcon name="database" :size="18" /> 数据管理</div>
    <DataTable />
  </div>
</template>
